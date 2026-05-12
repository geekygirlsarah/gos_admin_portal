"""Email + helper services for the application wizard."""
from __future__ import annotations

import logging
from typing import Iterable, List, Optional

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, send_mail
from django.db.models import Q
from django.template.loader import render_to_string
from django.urls import reverse

from .models import Application

logger = logging.getLogger(__name__)

LEAD_MENTOR_EMAIL = "leads@girlsofsteelrobotics.org"


def _from_email() -> str:
    return getattr(settings, "DEFAULT_FROM_EMAIL", "") or "noreply@example.com"


def _lead_mentor_email() -> str:
    return getattr(settings, "LEAD_MENTOR_NOTIFICATION_EMAIL", LEAD_MENTOR_EMAIL)


def _absolute_apply_url(request, application: Application) -> str:
    """Build an absolute URL the applicant can use to resume."""
    path = reverse("apply_resume_link", kwargs={"app_id": application.application_id})
    if request is not None:
        return request.build_absolute_uri(path)
    return path


def send_otp_email(application: Application, code: str, request=None) -> None:
    """Email the OTP code to the application's email."""
    if not application.email:
        logger.warning(
            "Refusing to send OTP for application %s: no email on file",
            application.application_id,
        )
        return
    subject = "Your Girls of Steel application verification code"
    body = (
        "Hi,\n\n"
        f"Your verification code is: {code}\n\n"
        "This code expires in 15 minutes. If you did not start an "
        "application with Girls of Steel, you can safely ignore this email.\n\n"
        f"Application ID: {application.application_id}\n"
    )
    send_mail(
        subject=subject,
        message=body,
        from_email=_from_email(),
        recipient_list=[application.email],
        fail_silently=False,
    )


def send_application_started_email(
    application: Application, request=None
) -> None:
    """Sent right after Step 2 so the applicant has their application ID
    even if they abandon the wizard before submitting.
    """
    if not application.email:
        return
    resume_url = _absolute_apply_url(request, application)
    subject = "You started a Girls of Steel application"
    body = (
        "Hi,\n\n"
        "Thanks for starting an application with Girls of Steel Robotics! "
        "You can resume it any time using:\n\n"
        f"  Application ID: {application.application_id}\n"
        f"  Resume link:    {resume_url}\n\n"
        "If you didn't start this, you can ignore this email.\n"
    )
    try:
        send_mail(
            subject=subject,
            message=body,
            from_email=_from_email(),
            recipient_list=[application.email],
            fail_silently=True,
        )
    except Exception:  # pragma: no cover - defensive
        logger.exception(
            "Failed to send application-started email for %s",
            application.application_id,
        )


def get_program_buckets():
    """Return (future, current, past) program querysets for the wizard.

    - future: start_date in the future or unknown active programs that haven't
      started yet — applications open.
    - current: started already and not ended — applications closed.
    - past: ended.
    """
    from django.utils import timezone

    from programs.models import Program

    today = timezone.localdate()
    future = Program.objects.filter(active=True, start_date__gt=today).order_by(
        "start_date", "name"
    )
    current = Program.objects.filter(
        active=True, start_date__lte=today
    ).exclude(end_date__lt=today).order_by("start_date", "name")
    past = Program.objects.filter(end_date__lt=today).order_by("-end_date", "name")
    return future, current, past


def emails_for_lookup(application: Application) -> Iterable[str]:
    """Emails to check against existing Student/Adult records.

    For students we should check the personal email *and* the Andrew email.
    Phase 1 only collects one email; this is a hook for later phases.
    """
    if application.email:
        yield application.email


# ---------------------------------------------------------------------------
# Lookup / prefill helpers (Step 5+)
# ---------------------------------------------------------------------------


def find_student_by_email(email: str):
    """Find a Student whose personal_email or andrew_email matches.

    Case-insensitive. Returns the first match or ``None``.
    """
    from programs.models import Student

    if not email:
        return None
    return (
        Student.objects.filter(
            Q(personal_email__iexact=email) | Q(andrew_email__iexact=email)
        ).first()
    )


def find_adult_by_email(email: str):
    """Find an Adult whose email/personal_email/andrew_email/alumni_email matches."""
    from programs.models import Adult

    if not email:
        return None
    return (
        Adult.objects.filter(
            Q(email__iexact=email)
            | Q(personal_email__iexact=email)
            | Q(andrew_email__iexact=email)
            | Q(alumni_email__iexact=email)
        ).first()
    )


def students_for_adult(adult) -> List:
    """Return the unique students this adult is connected to (primary,
    secondary, or any M2M relation)."""
    if adult is None:
        return []
    seen = {}
    for s in adult.primary_for.all():
        seen[s.pk] = s
    for s in adult.secondary_for.all():
        seen.setdefault(s.pk, s)
    # Reverse M2M from Student.adults
    for s in getattr(adult, "students", []).all() if hasattr(adult, "students") else []:
        seen.setdefault(s.pk, s)
    return list(seen.values())


def latest_program_for_student(student) -> Optional[str]:
    """Return a friendly label (e.g. "Summer 2024") for the most recent
    program this student has been enrolled in, or ``None`` if there isn't
    one.
    """
    if student is None:
        return None
    from programs.models import Enrollment

    enrollment = (
        Enrollment.objects.filter(student=student)
        .select_related("program")
        .order_by(
            "-program__start_date",
            "-program__year",
            "-created_at",
        )
        .first()
    )
    if enrollment is None or enrollment.program is None:
        return None
    return str(enrollment.program)


def latest_program_for_adult(adult) -> Optional[str]:
    """Return a friendly label for the most recent program any student
    linked to this adult was enrolled in, or ``None``."""
    if adult is None:
        return None
    students = students_for_adult(adult)
    best_label = None
    best_key = None
    from programs.models import Enrollment

    enrollment = (
        Enrollment.objects.filter(student__in=students)
        .select_related("program")
        .order_by(
            "-program__start_date",
            "-program__year",
            "-created_at",
        )
        .first()
    )
    if enrollment is None or enrollment.program is None:
        return None
    return str(enrollment.program)


def student_to_prefill(student) -> dict:
    """Convert a ``Student`` model into a dict suitable for ``StudentInfoForm``."""
    if student is None:
        return {}
    return {
        "legal_first_name": student.legal_first_name or "",
        "first_name": student.first_name or "",
        "last_name": student.last_name or "",
        "pronouns": student.pronouns or "",
        "date_of_birth": student.date_of_birth,
        "personal_email": student.personal_email or "",
        "cell_phone_number": student.cell_phone_number or "",
        "school_name": student.school.name if student.school_id else "",
        "graduation_year": student.graduation_year,
        "tshirt_size": student.tshirt_size or "",
        "allergies": student.allergies or "",
        "dietary_restrictions": student.dietary_restrictions or "",
        "medical_notes": student.medical_notes or "",
    }


def adult_to_prefill(adult) -> dict:
    """Convert an ``Adult`` model into a dict suitable for ``ParentInfoForm``."""
    if adult is None:
        return {}
    return {
        "first_name": adult.first_name or "",
        "last_name": adult.last_name or "",
        "relationship_to_student": adult.relationship_to_student or "",
        "email": adult.email or adult.personal_email or "",
        "cell_phone": adult.cell_phone or "",
        "home_phone": adult.home_phone or "",
    }


# ---------------------------------------------------------------------------
# Additional emails (Steps 6 & 8)
# ---------------------------------------------------------------------------


def _send_html_email(
    subject: str,
    text_body: str,
    html_body: Optional[str],
    recipients: List[str],
) -> None:
    if not recipients:
        return
    msg = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=_from_email(),
        to=recipients,
    )
    if html_body:
        msg.attach_alternative(html_body, "text/html")
    try:
        msg.send(fail_silently=False)
    except Exception:  # pragma: no cover - defensive
        logger.exception("Failed to send email %r to %r", subject, recipients)


def send_parent_handoff_email(
    application: Application, parent_email: str, request=None
) -> None:
    """Sent at Step 6 when a student started the application and the parent
    needs to take it over.
    """
    if not parent_email:
        return
    resume_url = _absolute_apply_url(request, application)
    ctx = {
        "application": application,
        "resume_url": resume_url,
    }
    text_body = render_to_string(
        "applications/email/parent_handoff.txt", ctx
    )
    html_body = render_to_string(
        "applications/email/parent_handoff.html", ctx
    )
    _send_html_email(
        subject="A Girls of Steel application is waiting for you",
        text_body=text_body,
        html_body=html_body,
        recipients=[parent_email],
    )


def _collect_applicant_recipients(application: Application) -> List[str]:
    """Return the list of email addresses for the submission-confirmation
    email: the student's email (if any) and the primary parent/guardian's
    email (from Step 6), deduplicated and case-insensitive.

    - Student applicant with their own email: both student + parent.
    - Student applicant who used the parent's email (no student email): just
      that one address (parent took over).
    - Parent applicant: their email plus the student's email if Step 5
      captured one different from the application email.
    """
    data = application.data or {}
    step5 = data.get("step5") or {}
    step6 = data.get("step6") or {}

    candidates: List[str] = []
    # Primary applicant email (whoever started / verified the OTP).
    if application.email:
        candidates.append(application.email)
    # Student's email captured on Step 5 (may differ from application.email
    # when a parent applied on behalf of a student who has their own email).
    student_email = (step5.get("email") or "").strip()
    if student_email:
        candidates.append(student_email)
    # Primary parent / guardian email from Step 6.
    parent_email = (step6.get("email") or "").strip()
    if parent_email:
        candidates.append(parent_email)

    seen = set()
    recipients: List[str] = []
    for addr in candidates:
        key = addr.lower()
        if key and key not in seen:
            seen.add(key)
            recipients.append(addr)
    return recipients


def send_application_submitted_email(
    application: Application, request=None
) -> None:
    """Confirmation email to the applicant on final submission.

    Sent to both the student and the primary parent/guardian (or to just
    the parent if the student doesn't have an email on file).
    """
    recipients = _collect_applicant_recipients(application)
    if not recipients:
        return
    resume_url = _absolute_apply_url(request, application)
    ctx = {
        "application": application,
        "resume_url": resume_url,
    }
    text_body = render_to_string(
        "applications/email/application_submitted.txt", ctx
    )
    html_body = render_to_string(
        "applications/email/application_submitted.html", ctx
    )
    _send_html_email(
        subject="Your Girls of Steel application has been submitted",
        text_body=text_body,
        html_body=html_body,
        recipients=recipients,
    )


def send_lead_notification_email(
    application: Application, request=None
) -> None:
    """Notify lead mentors that a new application was submitted."""
    recipient = _lead_mentor_email()
    if not recipient:
        return
    ctx = {
        "application": application,
        "applicant_data": application.data or {},
    }
    text_body = render_to_string(
        "applications/email/lead_notification.txt", ctx
    )
    html_body = render_to_string(
        "applications/email/lead_notification.html", ctx
    )
    _send_html_email(
        subject=f"New application: {application.application_id}",
        text_body=text_body,
        html_body=html_body,
        recipients=[recipient],
    )
