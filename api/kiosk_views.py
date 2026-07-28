import json
import logging

from django.contrib.auth import authenticate
from django.core.cache import cache
from django.db.models import Q
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST

from attendance.kiosk_utils import (
    _CODE_EXPIRY,
    _COOKIE_MAX_AGE,
    _cookie_name,
    _get_kiosk_or_404,
    _is_unlocked,
)
from attendance.models import RFIDCard, AttendanceSession, AttendanceEvent
from attendance.services import record_tap, get_attendance_stats
from programs.models import Adult, Student

logger = logging.getLogger(__name__)


@require_POST
def kiosk_request_code(request, kiosk_id):
    """POST /api/v1/kiosk/<id>/request_code/
    Accepts JSON {"email": "..."}.
    Generates a 6-digit code, stores it in cache, and sends it via email.
    """
    _get_kiosk_or_404(kiosk_id)
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"success": False, "error": "Invalid JSON."}, status=400)

    email = body.get("email", "").strip().lower()
    if not email:
        return JsonResponse(
            {"success": False, "error": "Email is required."}, status=400
        )

    # Use the portal's provisioning logic to ensure the user exists if they are allowed
    from allauth.account.adapter import get_adapter

    from GoSAdminPortal.adapter import _find_or_provision_user_for_email

    if not _find_or_provision_user_for_email(email):
        return JsonResponse(
            {
                "success": False,
                "error": "This email is not authorized to unlock kiosks.",
            },
            status=403,
        )

    # Generate a 6-digit code
    adapter = get_adapter()
    code = adapter.generate_login_code()

    # Store in cache
    cache_key = f"kiosk_otp_{kiosk_id}_{email}"
    cache.set(cache_key, code, _CODE_EXPIRY)

    # Send the email
    adapter.send_mail("account/email/login_code", email, {"code": code})

    return JsonResponse({"success": True})


@require_POST
def kiosk_unlock(request, kiosk_id):
    """POST /api/v1/kiosk/<id>/unlock/
    Accepts JSON {"email": "...", "code": "..."}.
    Verifies the code and sets a HttpOnly cookie to "unlock" the kiosk.
    """
    _get_kiosk_or_404(kiosk_id)
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"success": False, "error": "Invalid JSON."}, status=400)

    email = body.get("email", "").strip().lower()
    code = body.get("code", "").strip()

    if not email or not code:
        return JsonResponse(
            {"success": False, "error": "Email and code are required."}, status=400
        )

    cache_key = f"kiosk_otp_{kiosk_id}_{email}"
    stored_code = cache.get(cache_key)

    if not stored_code or stored_code != code:
        return JsonResponse(
            {"success": False, "error": "Invalid or expired code."}, status=403
        )

    # Clear the code from cache
    cache.delete(cache_key)

    # Set the unlock cookie
    response = JsonResponse({"success": True})
    response.set_cookie(
        _cookie_name(kiosk_id),
        "1",
        max_age=_COOKIE_MAX_AGE,
        httponly=True,
        samesite="Lax",
    )
    return response


@require_POST
def kiosk_lock(request, kiosk_id):
    """POST /api/v1/kiosk/<id>/lock/
    Clears the unlock cookie.
    """
    _get_kiosk_or_404(kiosk_id)
    response = JsonResponse({"success": True})
    response.delete_cookie(_cookie_name(kiosk_id))
    return response


@require_POST
def kiosk_tap(request, kiosk_id):
    """POST /api/v1/kiosk/<id>/tap/
    Records an attendance tap. Requires the unlock cookie.
    """
    config = _get_kiosk_or_404(kiosk_id)
    if not _is_unlocked(request, kiosk_id):
        return JsonResponse({"error": "Kiosk is locked."}, status=403)

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON."}, status=400)

    rfid_uid = body.get("rfid_uid", "")
    visitor_name = body.get("visitor_name", "")
    visitor_team_number = body.get("visitor_team_number")
    event_type = body.get("event_type", "AUTO")

    try:
        evt = record_tap(
            program=config.program,
            rfid_uid=rfid_uid,
            visitor_name=visitor_name,
            visitor_team_number=visitor_team_number,
            event_type=event_type,
            source="kiosk",
        )
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    student_name = None
    person_type = "visitor"
    if evt.student:
        student_name = str(evt.student)
        person_type = "student"
    elif evt.adult:
        student_name = str(evt.adult)
        person_type = "mentor"
    elif evt.visitor_name:
        student_name = evt.visitor_name

    res_data = {
        "event_type": evt.event_type,
        "occurred_at": evt.occurred_at.isoformat(),
        "student": student_name,
        "person_type": person_type,
    }

    if evt.event_type == AttendanceEvent.OUT:
        # Try to find the session that was just closed
        session = AttendanceSession.objects.filter(closed_by_event=evt).first()
        if session:
            res_data["session_hours"] = round(session.duration_minutes / 60.0, 1)

        # Also get weekly stats
        stats = get_attendance_stats(
            program=config.program,
            student=evt.student,
            adult=evt.adult,
            visitor_name=evt.visitor_name,
        )
        res_data["week_hours"] = stats["week_hours"]

        # If it's not a student, we don't want to gamify (remove hours)
        if person_type != "student":
            res_data.pop("session_hours", None)
            res_data.pop("week_hours", None)

    return JsonResponse(res_data)


@require_GET
def kiosk_lookup(request, kiosk_id):
    """GET /api/v1/kiosk/<id>/lookup/
    Student lookup by name or RFID. Requires the unlock cookie.
    """
    _get_kiosk_or_404(kiosk_id)
    if not _is_unlocked(request, kiosk_id):
        return JsonResponse({"error": "Kiosk is locked."}, status=403)

    rfid = request.GET.get("rfid", "").strip()
    name = request.GET.get("name", "").strip()

    results = []
    if rfid:
        try:
            card = RFIDCard.objects.select_related("student", "adult").get(
                uid=rfid, is_active=True
            )
            person = card.student or card.adult
            results = [
                {
                    "id": person.pk,
                    "name": getattr(person, "preferred_full_name", str(person)),
                    "type": "student" if card.student else "mentor",
                }
            ]
        except RFIDCard.DoesNotExist:
            results = []
    elif name:
        parts = name.split()
        # Search students
        student_qs = Student.objects.all()
        for part in parts:
            student_qs = student_qs.filter(
                Q(first_name__icontains=part)
                | Q(last_name__icontains=part)
                | Q(legal_first_name__icontains=part)
            )
        for s in student_qs[:10]:
            results.append(
                {
                    "id": s.pk,
                    "name": getattr(s, "preferred_full_name", str(s)),
                    "type": "student",
                }
            )

        # Search mentors/adults
        adult_qs = Adult.objects.filter(is_mentor=True)
        for part in parts:
            adult_qs = adult_qs.filter(
                Q(first_name__icontains=part) | Q(last_name__icontains=part)
            )
        for a in adult_qs[:10]:
            results.append(
                {
                    "id": a.pk,
                    "name": str(a),
                    "type": "mentor",
                }
            )

    return JsonResponse({"students": results})
