import base64
import binascii

from django.core.exceptions import ValidationError
from django.db.models import Value
from django.db.models.functions import Coalesce, Lower, NullIf
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie

from .models import DigitalSignout, DigitalSignoutConfig, StudentPresence
from .signout_utils import _is_unlocked


def _get_config_or_404(config_id):
    try:
        return DigitalSignoutConfig.objects.select_related("program").get(
            pk=config_id, is_active=True
        )
    except DigitalSignoutConfig.DoesNotExist:
        raise Http404("Sign-out station not found or inactive.")


def _active_students(program):
    """Active (non-graduated) students with an active enrollment in `program`."""
    return (
        program.students.select_related("user")
        .prefetch_related("adults")
        .filter(
            enrollment__program=program,
            enrollment__active=True,
            graduated=False,
        )
        .distinct()
        .annotate(
            sort_first=Lower(
                Coalesce(NullIf("preferred_first_name", Value("")), "legal_first_name")
            ),
            sort_last=Lower("last_name"),
        )
        .order_by("sort_first", "sort_last")
    )


def _decode_signature(data_url):
    """Decode a `data:image/png;base64,...` string to raw bytes.

    Returns raw bytes, or raises ``ValidationError`` if the payload isn't a
    valid PNG data URL.
    """
    prefix = "data:image/png;base64,"
    if not data_url or not data_url.startswith(prefix):
        raise ValidationError("Invalid signature image format.")
    b64 = data_url.removeprefix(prefix)
    try:
        raw = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError):
        raise ValidationError("Invalid signature image data.")
    if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValidationError("Signature must be a PNG image.")
    if len(raw) < 64:
        raise ValidationError("Signature appears to be empty.")
    return raw


def _station_url(config_id, query=""):
    """Relative URL to this station, with an optional query string."""
    path = reverse("digital_signout", args=[config_id])
    return f"{path}?{query}" if query else path


def _confirmation_url(request, config_id, signout_pk):
    """Absolute URL to this station's confirmation page for a signout."""
    return request.build_absolute_uri(_station_url(config_id, f"done={signout_pk}"))


def _upload_signature_bytes(instance, raw, ext="png"):
    """Write decoded PNG bytes to the model's FileField."""
    from django.core.files.base import ContentFile

    filename = f"signature_{timezone.now():%Y%m%d_%H%M%S%f}.{ext}"
    instance.signature.save(filename, ContentFile(raw), save=False)


@ensure_csrf_cookie
def digital_signout(request, config_id):
    """Public digital sign-out page (login-exempt tablet UI).

    The sign-out form is only shown once a mentor has unlocked the station from
    the program page (HttpOnly cookie). Otherwise an "unlock needed" message is
    shown. GET renders the pick-a-student + draw-a-signature form. POST
    validates the submission and records a :class:`DigitalSignout`. Students
    already signed out today are shown dimmed and can be undone with an
    ``undo`` POST (with confirmation) so a mistake can be corrected.
    """
    config = _get_config_or_404(config_id)
    program = config.program
    today = timezone.localdate()
    today_absent_ids = set(
        StudentPresence.objects.filter(
            program=program, date=today, status=StudentPresence.ABSENT
        ).values_list("student_id", flat=True)
    )
    students = [s for s in _active_students(program) if s.pk not in today_absent_ids]
    is_unlocked = _is_unlocked(request, config.pk)

    if not is_unlocked:
        return render(
            request,
            "kiosk/signout.html",
            {
                "config": config,
                "program": program,
                "is_unlocked": False,
            },
        )

    # Attach today's sign-out (if any) to each student so the template can dim
    # already-signed-out students and offer an undo.
    today_signouts = {
        so.student_id: so
        for so in DigitalSignout.objects.filter(
            config=config, signed_at__date=today
        ).select_related("student")
    }
    for student in students:
        student.today_signout = today_signouts.get(student.pk)

    # Parent/guardian display names per student, so the form can offer a
    # dropdown of who is signing out (from the student's linked accounts).
    parents_by_student = {
        s.pk: [a.display_name for a in s.adults.all() if a.is_parent] for s in students
    }

    def form_context(**extra):
        ctx = {
            "config": config,
            "program": program,
            "students": students,
            "parents_by_student": parents_by_student,
            "is_unlocked": True,
        }
        ctx.update(extra)
        return ctx

    if request.method == "POST":
        if request.POST.get("action") == "undo":
            return _handle_undo(request, config, students, form_context)
        return _handle_signout_submit(request, config, students, form_context)

    done = request.GET.get("done")
    confirmation = None
    if done:
        try:
            confirmation = DigitalSignout.objects.select_related("student").get(
                pk=int(done), config=config
            )
        except (ValueError, DigitalSignout.DoesNotExist):
            confirmation = None

    if confirmation is not None:
        return render(
            request,
            "kiosk/signout.html",
            form_context(success=True, signout=confirmation),
        )

    return render(
        request,
        "kiosk/signout.html",
        form_context(notice=request.GET.get("notice")),
    )


def _handle_undo(request, config, students, form_context):
    """Delete today's sign-out for this station (after user confirmation)."""
    signout_id = request.POST.get("signout_id")
    today = timezone.localdate()
    target = None
    if signout_id:
        try:
            target = DigitalSignout.objects.filter(
                config=config, signed_at__date=today
            ).get(pk=int(signout_id))
        except (ValueError, DigitalSignout.DoesNotExist):
            target = None

    if target is not None:
        target.delete()

    return redirect(_station_url(config.pk, "notice=undone"))


def _handle_signout_submit(request, config, students, form_context):
    """Validate and record a student sign-out (PRG -> confirmation URL)."""
    error = None
    student = None
    signed_by_name = (request.POST.get("signed_by_name") or "").strip()
    signature_payload = request.POST.get("signature") or ""

    student_id = request.POST.get("student_id")
    if student_id:
        student = next((s for s in students if str(s.pk) == str(student_id)), None)
        if student is None:
            error = "Please select one of your students to sign out."

    if not error and not signed_by_name:
        error = "Please enter your name."

    raw = None
    if not error:
        try:
            raw = _decode_signature(signature_payload)
        except ValidationError as exc:
            error = exc.messages[0]

    if not error and getattr(student, "today_signout", None):
        error = (
            f"{student.display_name} is already signed out today. Tap their "
            "name and confirm Undo to correct it."
        )

    if error:
        return render(
            request,
            "kiosk/signout.html",
            form_context(
                error=error,
                selected_student=student,
                signed_by_name=signed_by_name,
            ),
            status=400,
        )

    signout = DigitalSignout(
        config=config,
        program=config.program,
        student=student,
        signed_by_name=signed_by_name,
        signed_at=timezone.now(),
    )
    _upload_signature_bytes(signout, raw)
    signout.save()

    # PRG: redirect to a GET confirmation page (never re-render a POST).
    # Reloading / clicking "Done" afterwards is a plain GET, so the browser
    # never asks to re-submit form data and no duplicate signout is created.
    return redirect(_confirmation_url(request, config.pk, signout.pk))
