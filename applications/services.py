"""Email + helper services for the application wizard."""

from __future__ import annotations

import logging
import threading
from typing import Iterable, List, Optional

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, send_mail
from django.db import close_old_connections, transaction
from django.db.models import Q
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from programs.models import Adult, Enrollment, Program, Student

from .models import Application

logger = logging.getLogger(__name__)

LEAD_MENTOR_EMAIL = "leads@girlsofsteelrobotics.org"


def normalize_email(email: str) -> str:
    """Normalize an email address by lowering and stripping whitespace."""
    if not email:
        return ""
    return email.strip().lower()


def _from_email() -> str:
    email = getattr(settings, "DEFAULT_FROM_EMAIL", "") or "noreply@example.com"
    name = getattr(settings, "DEFAULT_FROM_NAME", None)
    if name:
        return f'"{name}" <{email}>'
    return email


def _lead_mentor_email() -> str:
    return getattr(settings, "LEAD_MENTOR_NOTIFICATION_EMAIL", LEAD_MENTOR_EMAIL)


def _absolute_apply_url(request, application: Application) -> str:
    """Build an absolute URL the applicant can use to resume."""
    path = reverse("apply_resume_link", kwargs={"app_id": application.application_id})
    if request is not None:
        return request.build_absolute_uri(path)
    return path


def _should_send_async() -> bool:
    """Backgrounding emails is a workaround for low-CPU environments (Render free tier).
    In tests, we want synchronous delivery to avoid race conditions in assertions.
    """

    if settings.EMAIL_BACKEND == "django.core.mail.backends.locmem.EmailBackend":
        return False
    # If the user has a custom setting, respect it
    return getattr(settings, "EMAIL_ASYNC", True)


def send_otp_email(application: Application, code: str, request=None) -> None:
    """Email the OTP code to the application's email, including resume info."""
    if not application.email:
        logger.warning(
            "Refusing to send OTP for application %s: no email on file",
            application.application_id,
        )
        return

    resume_url = _absolute_apply_url(request, application)
    subject = "Your Girls of Steel application verification code"
    body = (
        "Hi,\n\n"
        "Thanks for starting an application with Girls of Steel Robotics!\n\n"
        f"Your verification code is: {code}\n\n"
        "This code expires in 15 minutes.\n\n"
        "You can also resume your application any time using:\n"
        f"  Application ID: {application.application_id}\n"
        f"  Resume link:    {resume_url}\n\n"
        "If you did not start an application with Girls of Steel, you can safely ignore this email.\n"
    )

    def _send():
        try:
            send_mail(
                subject=subject,
                message=body,
                from_email=_from_email(),
                recipient_list=[application.email],
                fail_silently=False,
            )
        except Exception:
            logger.exception(
                "Failed to send OTP email for %s", application.application_id
            )
        finally:
            close_old_connections()

    if _should_send_async():
        threading.Thread(target=_send, name=f"otp-email-{application.pk}").start()
    else:
        _send()


def get_program_buckets():
    """Return (future, current, past) program querysets for the wizard.

    - future: start_date in the future or unknown active programs that haven't
      started yet — applications open.
    - current: started already and not ended — applications closed.
    - past: ended.
    """

    today = timezone.localdate()
    future = Program.objects.filter(active=True, start_date__gt=today).order_by(
        "start_date", "name"
    )
    current = (
        Program.objects.filter(active=True, start_date__lte=today)
        .exclude(end_date__lt=today)
        .order_by("start_date", "name")
    )
    past = Program.objects.filter(end_date__lt=today).order_by("-end_date", "name")
    return future, current, past


def emails_for_lookup(application: Application) -> Iterable[str]:
    """Emails to check against existing Student/Adult records.

    For students we should check the personal email *and* the Andrew email.
    Phase 1 only collects one email; this is a hook for later phases.
    """
    if application.email:
        yield application.email


def get_student_emails(application: Application) -> List[str]:
    """Collect all emails that might belong to the student in this application.
    Used to prevent students from using their own email for a parent.
    """
    emails = []
    # If the applicant is a student, the application email is theirs.
    if application.applicant_type == Application.Type.STUDENT and application.email:
        emails.append(application.email)

    # Check step 5 data (student info)
    step5_data = (application.data or {}).get("step5-student", {})
    personal_email = step5_data.get("personal_email")
    if personal_email:
        emails.append(personal_email)

    return list(set(emails))


# ---------------------------------------------------------------------------
# Lookup / prefill helpers (Step 5+)
# ---------------------------------------------------------------------------


def find_student_by_email(email: str):
    """Find a Student whose personal_email or andrew_email matches.

    Case-insensitive. Returns the first match or ``None``.
    """
    if not email:
        return None
    return Student.objects.filter(
        Q(personal_email__iexact=email) | Q(andrew_email__iexact=email)
    ).first()


def find_adult_by_email(email: str):
    """Find an Adult whose email/personal_email/andrew_email/alumni_email matches."""
    if not email:
        return None
    return Adult.objects.filter(
        Q(email__iexact=email)
        | Q(personal_email__iexact=email)
        | Q(andrew_email__iexact=email)
        | Q(alumni_email__iexact=email)
    ).first()


def find_existing_mentor_by_email(email: str):
    """Return an existing Adult flagged as a mentor matching this email,
    or ``None``. Used to block re-applications by people we already know.
    """
    adult = find_adult_by_email(email)
    if adult is not None and getattr(adult, "is_mentor", False):
        return adult
    return None


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
    for s in adult.students.all():
        seen.setdefault(s.pk, s)
    return list(seen.values())


def latest_program_for_student(student) -> Optional[Program]:
    """Return the most recent program this student has been enrolled in,
    or ``None`` if there isn't one.
    """
    if student is None:
        return None
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
    return enrollment.program


def latest_program_for_adult(adult) -> Optional[Program]:
    """Return the most recent program any student linked to this adult was enrolled in,
    or ``None``.
    """
    if adult is None:
        return None
    students = students_for_adult(adult)
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
    return enrollment.program


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
        "email_updates": adult.email_updates,
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

    def _do_send():
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
        finally:
            close_old_connections()

    if _should_send_async():
        threading.Thread(target=_do_send, name=f"html-email-{subject[:20]}").start()
    else:
        _do_send()


def send_parent_handoff_email(
    application: Application, parent_email: str, request=None
) -> None:
    """Sent at Step 6 when a student started the application and the parent
    needs to take it over.
    """
    if not parent_email:
        return

    # Use the token-authenticated resume link if available.
    if application.handoff_token:
        path = reverse(
            "apply_resume_link_with_token",
            kwargs={
                "app_id": application.application_id,
                "token": application.handoff_token,
            },
        )
    else:
        path = reverse(
            "apply_resume_link",
            kwargs={"app_id": application.application_id},
        )
    resume_url = request.build_absolute_uri(path) if request is not None else path

    ctx = {
        "application": application,
        "resume_url": resume_url,
    }
    text_body = render_to_string("applications/email/parent_handoff.txt", ctx)
    html_body = render_to_string("applications/email/parent_handoff.html", ctx)
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
    step5 = data.get("step5-student") or {}
    step7 = data.get("step7-primaryparent") or {}

    candidates: List[str] = []
    # Primary applicant email (whoever started / verified the OTP).
    if application.email:
        candidates.append(application.email)
    # Student's email captured on Step 5 (may differ from application.email
    # when a parent applied on behalf of a student who has their own email).
    student_email = (step5.get("email") or "").strip()
    if student_email:
        candidates.append(student_email)
    # Primary parent / guardian email from Step 7.
    parent_email = (step7.get("email") or "").strip()
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


def send_application_submitted_email(application: Application, request=None) -> None:
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
    text_body = render_to_string("applications/email/application_submitted.txt", ctx)
    html_body = render_to_string("applications/email/application_submitted.html", ctx)
    _send_html_email(
        subject="Your Girls of Steel application has been submitted",
        text_body=text_body,
        html_body=html_body,
        recipients=recipients,
    )


def send_application_approved_email(application: Application, request=None) -> None:
    """Notify the applicant that their application was approved and that
    they have signed documents to download/upload (Step 9).

    Recipients are the student + primary parent/guardian (or just the
    parent when the student has no email), matching the submission
    confirmation behavior. Called by the lead-mentor admin (to be built
    in a follow-up phase) when an application is marked APPROVED.
    """
    recipients = _collect_applicant_recipients(application)
    if not recipients:
        return
    # Link the applicant straight into Step 9 via the resume link, which
    # will redirect to /apply/<id>/step9/ for APPROVED applications.

    path = reverse("apply_resume_link", kwargs={"app_id": application.application_id})
    documents_url = request.build_absolute_uri(path) if request is not None else path
    ctx = {
        "application": application,
        "documents_url": documents_url,
    }
    text_body = render_to_string("applications/email/application_approved.txt", ctx)
    html_body = render_to_string("applications/email/application_approved.html", ctx)
    _send_html_email(
        subject="Your Girls of Steel application has been approved",
        text_body=text_body,
        html_body=html_body,
        recipients=recipients,
    )


def send_application_declined_email(
    application: Application, reason: str = "", request=None
) -> None:
    """Notify the applicant that their application was declined.

    Recipients are the student + primary parent/guardian (or just the
    parent when the student has no email), matching the submission
    confirmation behavior. Called by the lead-mentor review pages when
    an application is marked DECLINED.
    """
    recipients = _collect_applicant_recipients(application)
    if not recipients:
        return
    ctx = {
        "application": application,
        "reason": (reason or "").strip(),
    }
    text_body = render_to_string("applications/email/application_declined.txt", ctx)
    html_body = render_to_string("applications/email/application_declined.html", ctx)
    _send_html_email(
        subject="An update on your Girls of Steel application",
        text_body=text_body,
        html_body=html_body,
        recipients=recipients,
    )


class ApplicationConversionError(Exception):
    """Raised when an application cannot be converted to a Student."""


def _adult_from_data(parent_data: dict):
    """Find or create an Adult from a step7-primaryparent/step8-secondaryparent data dict.

    Match is by email (case-insensitive) when one is provided; otherwise a
    new Adult is created. Existing Adults have their blank/null fields
    filled in from the application data, but non-blank existing values
    are preserved (we trust what's already in the system).
    """
    from programs.models import Adult

    if not parent_data:
        return None
    email = (parent_data.get("email") or "").strip()
    first_name = (parent_data.get("first_name") or "").strip()
    last_name = (parent_data.get("last_name") or "").strip()
    if not email and not (first_name and last_name):
        return None

    adult = None
    if email:
        adult = Adult.objects.filter(email__iexact=email).first()
    if adult is None:
        adult = Adult(
            first_name=first_name or "(unknown)",
            last_name=last_name or "(unknown)",
            email=email or None,
            is_parent=True,
        )

    # Fill in any missing fields from the captured data, without
    # overwriting non-blank existing values.
    def _fill(field, value):
        value = (value or "").strip() if isinstance(value, str) else value
        if value and not getattr(adult, field, None):
            setattr(adult, field, value)

    _fill("first_name", first_name)
    _fill("preferred_first_name", parent_data.get("preferred_first_name"))
    _fill("last_name", last_name)
    _fill("email", email)
    _fill("cell_phone", parent_data.get("cell_phone"))
    _fill("home_phone", parent_data.get("home_phone"))
    _fill("address", parent_data.get("address"))
    _fill("city", parent_data.get("city"))
    _fill("state", parent_data.get("state"))
    _fill("zip_code", parent_data.get("zip_code"))
    _fill("pronouns", parent_data.get("pronouns"))

    # For boolean fields, only update if the existing value is False and we have a True value.
    if parent_data.get("email_updates") and not adult.email_updates:
        adult.email_updates = True

    rel = (parent_data.get("relationship_to_student") or "").strip().lower()
    if rel:
        # Only set if blank/default ("parent"); we don't try to map free-form
        # values against RELATIONSHIP_CHOICES.
        if (
            not adult.relationship_to_student
            or adult.relationship_to_student == "parent"
        ):
            adult.relationship_to_student = rel[:20]
    specific_rel = (parent_data.get("specific_relationship") or "").strip()
    if specific_rel and not adult.specific_relationship:
        adult.specific_relationship = specific_rel[:100]
    adult.is_parent = True
    adult.save()
    return adult


def _student_from_application(application: Application):
    """Find or create a Student from the application's step5 + email data.

    - If the wizard captured an `_existing_student_id`, that record is used.
    - Otherwise looks up by `personal_email`/`andrew_email` matching the
      application's email or step5 personal email.
    - Falls back to creating a new Student from the step5 form fields.
    """
    from programs.models import School, Student

    data = application.data or {}
    step5 = (data.get("step5-student") or {}).copy()
    existing_id = step5.pop("_existing_student_id", None)

    student = None
    if existing_id:
        student = Student.objects.filter(pk=existing_id).first()

    candidate_emails = [
        (application.email or "").strip(),
        (step5.get("personal_email") or "").strip(),
    ]
    candidate_emails = [e for e in candidate_emails if e]
    if student is None and candidate_emails:
        q = Q()
        for e in candidate_emails:
            q |= Q(personal_email__iexact=e) | Q(andrew_email__iexact=e)
        student = Student.objects.filter(q).first()

    if student is None:
        legal_first = (step5.get("legal_first_name") or "").strip()
        last_name = (step5.get("last_name") or "").strip()
        if not legal_first or not last_name:
            raise ApplicationConversionError(
                "Cannot create a Student: legal first name and last name "
                "are required in the application data (step 5)."
            )
        dob = step5.get("date_of_birth")
        if not dob:
            raise ApplicationConversionError(
                "Cannot create a Student: date of birth is required in the "
                "application data (step 5)."
            )
        student = Student(
            legal_first_name=legal_first,
            last_name=last_name,
            date_of_birth=dob,
        )

    # Apply / fill from step5 without overwriting non-blank existing values.
    def _fill(field, value):
        value = (value or "").strip() if isinstance(value, str) else value
        if value and not getattr(student, field, None):
            setattr(student, field, value)

    _fill("legal_first_name", step5.get("legal_first_name"))
    _fill("first_name", step5.get("first_name"))
    _fill("last_name", step5.get("last_name"))
    _fill("pronouns", step5.get("pronouns"))
    if step5.get("date_of_birth") and not student.date_of_birth:
        student.date_of_birth = step5["date_of_birth"]
    _fill("personal_email", step5.get("personal_email"))
    if "directory_consent" in step5:
        student.directory_consent = bool(step5["directory_consent"])
    _fill("cell_phone_number", step5.get("cell_phone_number"))
    _fill("address", step5.get("address"))
    _fill("city", step5.get("city"))
    _fill("state", step5.get("state"))
    _fill("zip_code", step5.get("zip_code"))
    if step5.get("graduation_year") and not student.graduation_year:
        try:
            student.graduation_year = int(step5["graduation_year"])
        except (TypeError, ValueError):
            pass
    _fill("tshirt_size", step5.get("tshirt_size"))
    _fill("allergies", step5.get("allergies"))
    _fill("dietary_restrictions", step5.get("dietary_restrictions"))
    _fill("medical_notes", step5.get("medical_notes"))

    # Experience / qualitative questions (from step6)
    step6 = data.get("step6-experience") or {}
    _fill("interest_reason", step6.get("interest_reason"))
    _fill("hoped_gains", step6.get("hoped_gains"))
    _fill("prior_robotics_experience", step6.get("prior_robotics_experience"))
    _fill("referral_source", step6.get("referral_source"))

    # School: free-text -> lookup-or-create.
    school_name = (step5.get("school_name") or "").strip()
    if school_name and not student.school_id:
        school, _ = School.objects.get_or_create(name=school_name)
        student.school = school

    student.save()

    # M2M Race ethnicities (only if student didn't already have some on file)
    race_ids = step5.get("race_ethnicities")
    if race_ids and not student.race_ethnicities.exists():
        student.race_ethnicities.set(race_ids)

    return student


def convert_application_to_student(application: Application, request=None):
    """Convert an APPROVED_SIGNED application into a real Student record.

    Creates / updates Student + Adult records, links primary/secondary
    contacts, creates an Enrollment in the application's program, and
    flips the application status to CONVERTED. Idempotent: if the
    application has already been converted, returns the existing
    student.
    """

    from programs.models import Enrollment

    if application.converted_student_id:
        return application.converted_student

    if application.status not in (
        Application.Status.APPROVED,
        Application.Status.APPROVED_SIGNED,
    ):
        raise ApplicationConversionError(
            "Only approved applications can be converted to a student."
        )
    # If approved (not yet APPROVED_SIGNED), verify there are no
    # required signed documents still missing for the program.
    if application.status == Application.Status.APPROVED and application.program_id:
        from programs.models import ProgramDocument

        required_docs = ProgramDocument.objects.filter(
            program_id=application.program_id, is_active=True, is_required=True
        )
        submitted_ids = set(
            application.document_submissions.values_list("document_id", flat=True)
        )
        missing = [d for d in required_docs if d.id not in submitted_ids]
        if missing:
            names = ", ".join(d.name for d in missing)
            raise ApplicationConversionError(
                "Cannot convert: required signed documents are still "
                f"missing: {names}."
            )
    if not application.program_id:
        raise ApplicationConversionError(
            "Application has no program selected; cannot enroll."
        )

    data = application.data or {}
    with transaction.atomic():
        primary = _adult_from_data(data.get("step7-primaryparent") or {})
        secondary = _adult_from_data(data.get("step8-secondaryparent") or {})

        student = _student_from_application(application)

        # Link contacts if not already set on the student.
        changed = False
        if primary and not student.primary_contact_id:
            student.primary_contact = primary
            changed = True
        if secondary and not student.secondary_contact_id:
            student.secondary_contact = secondary
            changed = True
        if changed:
            student.save()

        # Ensure bi-directional M2M relationship is also established.
        if primary:
            student.adults.add(primary)
        if secondary:
            student.adults.add(secondary)

        Enrollment.objects.get_or_create(student=student, program=application.program)

        application.converted_student = student
        application.converted_at = timezone.now()
        application.status = Application.Status.CONVERTED
        application.save(
            update_fields=[
                "converted_student",
                "converted_at",
                "status",
                "updated_at",
            ]
        )

    return student


def send_lead_notification_email(application: Application, request=None) -> None:
    """Notify lead mentors that a new application was submitted."""
    recipient = _lead_mentor_email()
    if not recipient:
        return
    ctx = {
        "application": application,
        "applicant_data": application.data or {},
    }
    text_body = render_to_string("applications/email/lead_notification.txt", ctx)
    html_body = render_to_string("applications/email/lead_notification.html", ctx)
    _send_html_email(
        subject=f"New application: {application.application_id}",
        text_body=text_body,
        html_body=html_body,
        recipients=[recipient],
    )
