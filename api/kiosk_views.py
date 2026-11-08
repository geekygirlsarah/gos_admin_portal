import json
import logging
import secrets

from django.core.cache import cache
from django.db.models import Exists, OuterRef, Q
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST

from attendance.kiosk_utils import (
    _CODE_ATTEMPT_LIMIT,
    _CODE_ATTEMPT_WINDOW,
    _CODE_EXPIRY,
    _CODE_REQUEST_IP_LIMIT,
    _CODE_REQUEST_LIMIT,
    _CODE_REQUEST_WINDOW,
    _acquire_redemption_lock,
    _cookie_name,
    _get_eligible_unlock_user,
    _get_kiosk_or_404,
    _increment_rate_limit,
    _is_unlocked,
    _make_unlock_token,
    _otp_cache_key,
    _otp_ready_cache_key,
    _rate_limit_cache_key,
    _unlock_cookie_options,
)
from attendance.models import AttendanceEvent, RFIDCard
from attendance.services import (
    attendance_sessions_for_program,
    get_attendance_stats,
    record_tap,
)
from programs.models import Adult, Enrollment, Student
from programs.utils import active_mentors, active_students_in_program

logger = logging.getLogger(__name__)


def _active_students_for_kiosk(config):
    tenant_enrollment = Enrollment.objects.filter(
        organization=config.organization,
        program=config.program,
        student_id=OuterRef("pk"),
        active=True,
    )
    return (
        active_students_in_program(config.program, organization=config.organization)
        .filter(Exists(tenant_enrollment))
        .distinct()
    )


def _person_first_name(student=None, adult=None):
    """Return the display first name for a student or adult.

    Students: ``preferred_first_name`` → ``legal_first_name``.
    Adults/Mentors: ``preferred_first_name`` → ``legal_first_name``.
    """
    if student:
        return student.preferred_first_name or student.legal_first_name
    if adult:
        return adult.preferred_first_name or adult.legal_first_name
    return ""


def _person_full_name(student=None, adult=None):
    """Return the full name for a student or adult.

    Uses the same preferred-name logic as ``_person_first_name`` but appends
    the last name (used on the Who's Here screen for emergencies).
    """
    first = _person_first_name(student=student, adult=adult)
    last = (student or adult).last_name
    return f"{first} {last}".strip()


@require_GET
def kiosk_ping(request, kiosk_id):
    """GET /api/v1/kiosk/<id>/ping/
    Lightweight health check. Verifies the kiosk config exists and the server is up.
    """
    config = _get_kiosk_or_404(kiosk_id, getattr(request, "organization", None))
    return JsonResponse({"ok": True, "unlocked": _is_unlocked(request, config)})


@require_POST
def kiosk_request_code(request, kiosk_id):
    """POST /api/v1/kiosk/<id>/request_code/
    Accepts JSON {"email": "..."}.
    Generates a 6-digit code, stores it in cache, and sends it via email.
    """
    kiosk = _get_kiosk_or_404(kiosk_id, getattr(request, "organization", None))
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"success": False, "error": "Invalid JSON."}, status=400)

    email = body.get("email", "").strip().lower()
    if not email:
        return JsonResponse(
            {"success": False, "error": "Email is required."}, status=400
        )

    remote_addr = request.META.get("REMOTE_ADDR", "")
    ip_limit = _increment_rate_limit(
        _rate_limit_cache_key("code_ip", kiosk, remote_addr),
        _CODE_REQUEST_IP_LIMIT,
        _CODE_REQUEST_WINDOW,
    )
    if ip_limit is None:
        return JsonResponse(
            {"success": False, "error": "Temporarily unavailable."}, status=503
        )
    if not ip_limit:
        return JsonResponse(
            {"success": False, "error": "Too many requests."}, status=429
        )

    user = _get_eligible_unlock_user(email, kiosk.organization)
    if user is None:
        return JsonResponse(
            {
                "success": False,
                "error": "This email is not authorized to unlock kiosks.",
            },
            status=403,
        )
    email = user.email.strip().lower()

    email_limit = _increment_rate_limit(
        _rate_limit_cache_key("code_request", kiosk, email),
        _CODE_REQUEST_LIMIT,
        _CODE_REQUEST_WINDOW,
    )
    if email_limit is None:
        return JsonResponse(
            {"success": False, "error": "Temporarily unavailable."}, status=503
        )
    if not email_limit:
        return JsonResponse(
            {"success": False, "error": "Too many requests."}, status=429
        )

    # Generate a 6-digit code
    from allauth.account.adapter import get_adapter

    adapter = get_adapter()
    code = adapter.generate_login_code()

    cache_key = _otp_cache_key(kiosk, email)
    nonce = secrets.token_urlsafe(16)
    record = {
        "code": code,
        "nonce": nonce,
        "user_id": user.pk,
    }
    try:
        cache.set(cache_key, record, _CODE_EXPIRY)
        adapter.send_mail("account/email/login_code", user.email, {"code": code})
        if not cache.add(_otp_ready_cache_key(kiosk, email, nonce), True, _CODE_EXPIRY):
            raise RuntimeError("Unable to mark kiosk unlock code ready.")
    except Exception:
        logger.warning("Unable to send a kiosk unlock code.")
        return JsonResponse(
            {"success": False, "error": "Unable to send code."}, status=503
        )

    return JsonResponse({"success": True})


@require_POST
def kiosk_unlock(request, kiosk_id):
    """POST /api/v1/kiosk/<id>/unlock/
    Accepts JSON {"email": "...", "code": "..."}.
    Verifies the code and sets a HttpOnly cookie to "unlock" the kiosk.
    """
    kiosk = _get_kiosk_or_404(kiosk_id, getattr(request, "organization", None))
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

    email = email.lower()
    remote_addr = request.META.get("REMOTE_ADDR", "")
    attempt_limits = (
        _increment_rate_limit(
            _rate_limit_cache_key("code_attempt_email", kiosk, email),
            _CODE_ATTEMPT_LIMIT,
            _CODE_ATTEMPT_WINDOW,
        ),
        _increment_rate_limit(
            _rate_limit_cache_key("code_attempt_ip", kiosk, remote_addr),
            _CODE_ATTEMPT_LIMIT * 5,
            _CODE_ATTEMPT_WINDOW,
        ),
    )
    if None in attempt_limits:
        return JsonResponse(
            {"success": False, "error": "Temporarily unavailable."}, status=503
        )
    if not all(attempt_limits):
        return JsonResponse(
            {"success": False, "error": "Too many attempts."}, status=429
        )

    cache_key = _otp_cache_key(kiosk, email)
    try:
        record = cache.get(cache_key)
        if (
            not isinstance(record, dict)
            or not isinstance(record.get("code"), str)
            or not isinstance(record.get("nonce"), str)
            or not code.isascii()
            or not secrets.compare_digest(record["code"], code)
        ):
            return JsonResponse(
                {"success": False, "error": "Invalid or expired code."}, status=403
            )

        readiness_key = _otp_ready_cache_key(kiosk, email, record["nonce"])
        if cache.get(readiness_key) is not True:
            return JsonResponse(
                {"success": False, "error": "Invalid or expired code."}, status=403
            )

        lock = _acquire_redemption_lock(kiosk, email, record["nonce"])
        if lock is None:
            return JsonResponse(
                {"success": False, "error": "Temporarily unavailable."}, status=503
            )
        if (
            not lock
            or cache.get(cache_key) != record
            or cache.get(readiness_key) is not True
        ):
            return JsonResponse(
                {"success": False, "error": "Invalid or expired code."}, status=403
            )

        user = _get_eligible_unlock_user(email, kiosk.organization)
        if user is None or user.pk != record.get("user_id"):
            return JsonResponse(
                {"success": False, "error": "This account is not authorized."},
                status=403,
            )
    except Exception:
        logger.warning("Unable to verify a kiosk unlock code.")
        return JsonResponse(
            {"success": False, "error": "Temporarily unavailable."}, status=503
        )

    # Set the unlock cookie
    response = JsonResponse({"success": True})
    response.set_cookie(
        _cookie_name(kiosk_id),
        _make_unlock_token(kiosk),
        **_unlock_cookie_options(),
    )
    return response


@require_POST
def kiosk_lock(request, kiosk_id):
    """POST /api/v1/kiosk/<id>/lock/
    Clears the unlock cookie.
    """
    _get_kiosk_or_404(kiosk_id, getattr(request, "organization", None))
    response = JsonResponse({"success": True})
    response.delete_cookie(_cookie_name(kiosk_id))
    return response


@require_POST
def kiosk_tap(request, kiosk_id):
    """POST /api/v1/kiosk/<id>/tap/
    Records an attendance tap. Requires the unlock cookie.
    """
    config = _get_kiosk_or_404(kiosk_id, getattr(request, "organization", None))
    if not _is_unlocked(request, config):
        return JsonResponse({"error": "Kiosk is locked."}, status=403)

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON."}, status=400)

    rfid_uid = body.get("rfid_uid", "")
    visitor_name = body.get("visitor_name", "")
    visitor_team_number = body.get("visitor_team_number")
    student_id = body.get("student_id")
    adult_id = body.get("adult_id")
    event_type = body.get("event_type", "AUTO")

    student = None
    adult = None
    if student_id:
        try:
            student = _active_students_for_kiosk(config).get(pk=student_id)
        except Student.DoesNotExist:
            return JsonResponse({"error": "Student not found or inactive."}, status=404)
    if adult_id:
        try:
            adult = active_mentors(organization=config.organization).get(pk=adult_id)
        except Adult.DoesNotExist:
            return JsonResponse({"error": "Adult not found or inactive."}, status=404)

    # If rfid_uid or specific IDs are provided, we MUST resolve to a member.
    # This assumes Member Sign-In tab usage.
    if rfid_uid or student or adult:
        if not (student or adult) and rfid_uid:
            from attendance.services import resolve_person_by_uid

            person = resolve_person_by_uid(rfid_uid, organization=config.organization)
            if isinstance(person, Student):
                try:
                    student = _active_students_for_kiosk(config).get(pk=person.pk)
                except Student.DoesNotExist:
                    return JsonResponse(
                        {"error": "Student not found or inactive."}, status=404
                    )
            elif isinstance(person, Adult):
                try:
                    adult = active_mentors(organization=config.organization).get(
                        pk=person.pk
                    )
                except Adult.DoesNotExist:
                    return JsonResponse(
                        {"error": "Adult not found or inactive."}, status=404
                    )

        if not (student or adult):
            return JsonResponse({"error": "Member not found."}, status=400)
    else:
        if not isinstance(visitor_name, str):
            return JsonResponse(
                {"error": "Visitor name must include at least two parts."}, status=400
            )
        visitor_name = visitor_name.strip()
        if len(visitor_name.split()) < 2:
            return JsonResponse(
                {"error": "Visitor name must include at least two parts."}, status=400
            )

    try:
        evt = record_tap(
            program=config.program,
            rfid_uid=rfid_uid,
            student=student,
            adult=adult,
            visitor_name=visitor_name,
            visitor_team_number=visitor_team_number,
            event_type=event_type,
            source="kiosk",
        )

    except Exception as exc:
        logger.exception("Error recording kiosk tap: %s", exc)
        return JsonResponse({"error": "Unable to process tap request."}, status=400)

    student_name = None
    person_type = "visitor"
    if evt.student:
        student_name = _person_first_name(student=evt.student)
        person_type = "student"
    elif evt.adult:
        student_name = _person_first_name(adult=evt.adult)
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
        session = (
            attendance_sessions_for_program(config.program)
            .filter(closed_by_event=evt)
            .first()
        )
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
    config = _get_kiosk_or_404(kiosk_id, getattr(request, "organization", None))
    if not _is_unlocked(request, config):
        return JsonResponse({"error": "Kiosk is locked."}, status=403)

    rfid = request.GET.get("rfid", "").strip()
    name = request.GET.get("name", "").strip()

    results = []
    if rfid:
        from attendance.services import resolve_card_by_uid

        card = resolve_card_by_uid(rfid, organization=config.organization)
        if card:
            if card.student:
                if (
                    _active_students_for_kiosk(config)
                    .filter(pk=card.student.pk)
                    .exists()
                ):
                    results = [
                        {
                            "id": card.student.pk,
                            "name": _person_first_name(student=card.student),
                            "type": "student",
                        }
                    ]
            elif card.adult:
                if (
                    active_mentors(organization=config.organization)
                    .filter(pk=card.adult.pk)
                    .exists()
                ):
                    results = [
                        {
                            "id": card.adult.pk,
                            "name": _person_first_name(adult=card.adult),
                            "type": "mentor",
                        }
                    ]
    elif name:
        parts = name.split()
        # Search students
        if len(parts) >= 2:
            student_qs = _active_students_for_kiosk(config).filter(
                (
                    Q(preferred_first_name__iexact=parts[0])
                    | Q(legal_first_name__iexact=parts[0])
                ),
                last_name__iexact=" ".join(parts[1:]),
            )
            for s in student_qs[:10]:
                results.append(
                    {
                        "id": s.pk,
                        "name": _person_first_name(student=s),
                        "type": "student",
                    }
                )

        # Search mentors/adults by exact first and full last name.
        if len(parts) >= 2:
            adult_qs = active_mentors(organization=config.organization).filter(
                (
                    Q(legal_first_name__iexact=parts[0])
                    | Q(preferred_first_name__iexact=parts[0])
                ),
                last_name__iexact=" ".join(parts[1:]),
            )
            for a in adult_qs[:10]:
                results.append(
                    {
                        "id": a.pk,
                        "name": _person_first_name(adult=a),
                        "type": "mentor",
                    }
                )

    return JsonResponse({"students": results})


@require_GET
def kiosk_who_is_here(request, kiosk_id):
    """GET /api/v1/kiosk/<id>/who-is-here/
    Returns list of people currently signed in. Requires the unlock cookie.
    """
    config = _get_kiosk_or_404(kiosk_id, getattr(request, "organization", None))
    if not _is_unlocked(request, config):
        return JsonResponse({"error": "Kiosk is locked."}, status=403)

    sessions = (
        attendance_sessions_for_program(config.program)
        .filter(check_out__isnull=True)
        .select_related("student", "adult")
    )

    results = []
    for s in sessions:
        name = ""
        person_type = ""
        if s.student:
            name = _person_full_name(student=s.student)
            person_type = "student"
        elif s.adult:
            name = _person_full_name(adult=s.adult)
            person_type = "mentor"
        else:
            name = s.visitor_name
            if s.visitor_team_number:
                name += f" (Team {s.visitor_team_number})"
            person_type = "visitor"

        results.append(
            {
                "name": name,
                "type": person_type,
                "check_in": s.check_in.isoformat(),
            }
        )

    # Sort by name
    results.sort(key=lambda x: x["name"])

    return JsonResponse({"people": results})
