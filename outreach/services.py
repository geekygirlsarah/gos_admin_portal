"""Status and reminder emails for the outreach feature.

Handles confirmation emails when students or mentors sign up for an outreach
event (or when staff updates signups), complete with event details and an
attached iCalendar (.ics) file. Also handles automated reminders.
"""

import logging
from datetime import datetime, time

from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

from organizations.utils import get_organization_email_settings
from programs.utils.notifications import send_templated_notification

logger = logging.getLogger(__name__)


def student_recipient_email(student):
    """Return the primary email address to reach ``student``."""
    if not student:
        return None
    email = (
        student.personal_email
        or student.org_email
        or (student.user.email if getattr(student, "user", None) else None)
    )
    if email and email.strip():
        return email.strip()
    return None


def adult_recipient_email(adult):
    """Return the primary email address to reach ``adult``."""
    if not adult:
        return None
    email = (
        adult.personal_email
        or adult.org_email
        or (adult.user.email if getattr(adult, "user", None) else None)
    )
    if email and email.strip():
        return email.strip()
    return None


def _trusted_notification_organization(signup, profile):
    shift = signup.shift
    event = shift.event
    organization = getattr(event, "organization", None)
    if organization is None or profile is None:
        return None
    if (
        signup.organization_id != organization.pk
        or shift.organization_id != organization.pk
        or profile.organization_id != organization.pk
        or (event.program_id and event.program.organization_id != organization.pk)
    ):
        return None
    return organization


def outreach_link(event):
    """Return the tenant's absolute outreach URL, or None when unavailable."""
    if not event or not event.program_id or not event.organization_id:
        return None
    if event.program.organization_id != event.organization_id:
        return None
    base = get_organization_email_settings(event.organization).get("portal_url", "")
    if not base:
        return None
    path = reverse("outreach:event_list", kwargs={"program_id": event.program_id})
    return f"{base.rstrip('/')}{path}"


def generate_shift_ics(shift, role_label=None):
    """Generate an RFC-5545 compliant iCalendar string for an outreach shift."""
    event = shift.event
    tz_name = getattr(settings, "TIME_ZONE", "America/New_York")

    start_t = shift.start_time or time.min
    end_t = shift.end_time or time.max

    start_dt = datetime.combine(shift.date, start_t)
    end_dt = datetime.combine(shift.date, end_t)

    from_domain = getattr(settings, "DEFAULT_FROM_EMAIL", "gosadminportal")
    if "@" in from_domain:
        from_domain = from_domain.split("@")[-1]
    uid = f"outreach-shift-{shift.pk}@{from_domain}"
    dtstamp = timezone.now().strftime("%Y%m%dT%H%M%SZ")

    summary = event.name or "Outreach Event"
    if role_label:
        summary = f"{summary} ({role_label})"

    location_parts = []
    if event.location_name:
        location_parts.append(event.location_name)
    if event.location_address:
        location_parts.append(event.location_address)
    location = ", ".join(location_parts)

    description_parts = []
    if event.description:
        description_parts.append(event.description)
    if role_label:
        description_parts.append(f"Role: {role_label}")
    if event.program:
        description_parts.append(f"Program: {event.program.name}")

    def _escape(text):
        return (
            text.replace("\\", "\\\\")
            .replace(";", "\\;")
            .replace(",", "\\,")
            .replace("\r\n", "\\n")
            .replace("\n", "\\n")
        )

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//GoS Admin Portal//Outreach//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{dtstamp}",
        f"DTSTART;TZID={tz_name}:{start_dt.strftime('%Y%m%dT%H%M%S')}",
        f"DTEND;TZID={tz_name}:{end_dt.strftime('%Y%m%dT%H%M%S')}",
        f"SUMMARY:{_escape(summary)}",
    ]
    if description_parts:
        desc_str = _escape("\n\n".join(description_parts))
        lines.append(f"DESCRIPTION:{desc_str}")
    if location:
        lines.append(f"LOCATION:{_escape(location)}")
    lines.extend(
        [
            "END:VEVENT",
            "END:VCALENDAR",
        ]
    )
    return "\r\n".join(lines)


def _shift_ics_attachment(shift, role_label=None, include_shift_id=False):
    ics_content = generate_shift_ics(shift, role_label=role_label)
    filename = slugify(shift.event.name or "outreach-event")
    if include_shift_id:
        filename = f"{filename}-shift-{shift.pk}"
    filename = f"{filename}.ics"
    return (filename, ics_content, "text/calendar")


def _shift_participants(shift):
    """Return lists of display names for participants on this shift."""
    from outreach.models import OutreachSignup

    event = shift.event
    organization_id = shift.organization_id
    if event.organization_id != organization_id or (
        event.program_id and event.program.organization_id != organization_id
    ):
        return {"champions": [], "helpers": [], "mentors": []}

    if "signups" in getattr(shift, "_prefetched_objects_cache", {}):
        signups = shift.signups.all()
    else:
        signups = shift.signups.filter(
            organization_id=organization_id,
            student__organization_id=organization_id,
        ).select_related("student")
    if "mentor_signups" in getattr(shift, "_prefetched_objects_cache", {}):
        mentor_signups = shift.mentor_signups.all()
    else:
        mentor_signups = shift.mentor_signups.filter(
            organization_id=organization_id,
            adult__organization_id=organization_id,
        ).select_related("adult")

    signups = [
        signup
        for signup in signups
        if signup.organization_id == organization_id
        and signup.student.organization_id == organization_id
    ]
    mentor_signups = [
        signup
        for signup in mentor_signups
        if signup.organization_id == organization_id
        and signup.adult.organization_id == organization_id
    ]
    champions = [
        signup.student.display_name
        for signup in signups
        if signup.role == OutreachSignup.CHAMPION
    ]
    helpers = [
        signup.student.display_name
        for signup in signups
        if signup.role == OutreachSignup.HELPER
    ]
    mentors = [signup.adult.display_name for signup in mentor_signups]
    return {
        "champions": champions,
        "helpers": helpers,
        "mentors": mentors,
    }


def _reminder_shift(signup, shifts_by_id=None):
    if shifts_by_id and signup.shift_id in shifts_by_id:
        return shifts_by_id[signup.shift_id]
    return signup.shift


def _base_signup_context(signup, reminder_signups=None, shifts_by_id=None):
    shift = _reminder_shift(signup, shifts_by_id)
    event = shift.event
    student = signup.student
    ctx = {
        "signup": signup,
        "shift": shift,
        "event": event,
        "student": student,
        "program": event.program,
        "role": signup.role,
        "role_display": signup.get_role_display(),
        "outreach_link": outreach_link(event),
    }
    if reminder_signups is None:
        ctx.update(_shift_participants(shift))
    else:
        shift_contexts = []
        for reminder_signup in reminder_signups:
            reminder_shift = _reminder_shift(reminder_signup, shifts_by_id)
            shift_context = {
                "shift": reminder_shift,
                "role_display": reminder_signup.get_role_display(),
            }
            shift_context.update(_shift_participants(reminder_shift))
            shift_contexts.append(shift_context)
            if reminder_signup.pk == signup.pk:
                ctx.update(
                    {
                        key: shift_context[key]
                        for key in ("champions", "helpers", "mentors")
                    }
                )
        ctx["shifts"] = shift_contexts
    return ctx


def _base_mentor_signup_context(
    mentor_signup, reminder_signups=None, shifts_by_id=None
):
    shift = _reminder_shift(mentor_signup, shifts_by_id)
    event = shift.event
    adult = mentor_signup.adult
    ctx = {
        "signup": mentor_signup,
        "shift": shift,
        "event": event,
        "adult": adult,
        "program": event.program,
        "role": "mentor",
        "role_display": "Mentor Support",
        "outreach_link": outreach_link(event),
    }
    if reminder_signups is None:
        ctx.update(_shift_participants(shift))
    else:
        shift_contexts = []
        for reminder_signup in reminder_signups:
            reminder_shift = _reminder_shift(reminder_signup, shifts_by_id)
            shift_context = {"shift": reminder_shift}
            shift_context.update(_shift_participants(reminder_shift))
            shift_contexts.append(shift_context)
            if reminder_signup.pk == mentor_signup.pk:
                ctx.update(
                    {
                        key: shift_context[key]
                        for key in ("champions", "helpers", "mentors")
                    }
                )
        ctx["shifts"] = shift_contexts
    return ctx


def _matching_reminder_signups(signup, signups, profile_field, shifts_by_id=None):
    profile_id = getattr(signup, f"{profile_field}_id")
    signup_shift = _reminder_shift(signup, shifts_by_id)
    matching_signups = []
    seen_signup_ids = set()
    for candidate in signups or [signup]:
        candidate_shift = _reminder_shift(candidate, shifts_by_id)
        if (
            candidate.pk not in seen_signup_ids
            and candidate.organization_id == signup.organization_id
            and candidate_shift.organization_id == signup_shift.organization_id
            and candidate_shift.event_id == signup_shift.event_id
            and candidate_shift.date == signup_shift.date
            and getattr(candidate, f"{profile_field}_id") == profile_id
        ):
            matching_signups.append(candidate)
            seen_signup_ids.add(candidate.pk)
    if signup.pk not in seen_signup_ids:
        matching_signups.insert(0, signup)
    return matching_signups


def notify_outreach_signup(signup):
    """Student signed up for an outreach shift (or was added by staff)."""
    recipient = student_recipient_email(signup.student)
    if not recipient:
        return False

    context = _base_signup_context(signup)
    event_name = signup.shift.event.name
    role_display = signup.get_role_display()
    subject = f"Outreach Sign-up: {event_name} ({role_display})"
    attachment = _shift_ics_attachment(signup.shift, role_label=role_display)

    try:
        send_templated_notification(
            subject=subject,
            template_name="outreach/emails/signup_confirmation.html",
            context=context,
            recipient_list=[recipient],
            attachments=[attachment],
            organization=_trusted_notification_organization(signup, signup.student),
        )
        return True
    except Exception as exc:
        logger.error(
            "Outreach signup confirmation dispatch failed (%s).", type(exc).__name__
        )
        return False


def notify_mentor_outreach_signup(mentor_signup):
    """Mentor signed up to support an outreach shift (or was added by lead mentor)."""
    recipient = adult_recipient_email(mentor_signup.adult)
    if not recipient:
        return False

    context = _base_mentor_signup_context(mentor_signup)
    event_name = mentor_signup.shift.event.name
    subject = f"Outreach Sign-up: {event_name} (Mentor Support)"
    attachment = _shift_ics_attachment(mentor_signup.shift, role_label="Mentor Support")

    try:
        send_templated_notification(
            subject=subject,
            template_name="outreach/emails/mentor_signup_confirmation.html",
            context=context,
            recipient_list=[recipient],
            attachments=[attachment],
            organization=_trusted_notification_organization(
                mentor_signup, mentor_signup.adult
            ),
        )
        return True
    except Exception as exc:
        logger.error(
            "Outreach mentor signup confirmation dispatch failed (%s).",
            type(exc).__name__,
        )
        return False


def notify_outreach_reminder(signup, signups=None, shifts_by_id=None):
    """Send an outreach shift reminder email to a student."""
    recipient = student_recipient_email(signup.student)
    if not recipient:
        return False

    signups = _matching_reminder_signups(
        signup, signups, "student", shifts_by_id=shifts_by_id
    )
    context = _base_signup_context(
        signup, reminder_signups=signups, shifts_by_id=shifts_by_id
    )
    shift = _reminder_shift(signup, shifts_by_id)
    event_name = shift.event.name
    subject = f"Reminder: Outreach event {event_name} is tomorrow!"
    attachments = [
        _shift_ics_attachment(
            _reminder_shift(item, shifts_by_id),
            role_label=item.get_role_display(),
            include_shift_id=len(signups) > 1,
        )
        for item in signups
    ]

    try:
        send_templated_notification(
            subject=subject,
            template_name="outreach/emails/reminder.html",
            context=context,
            recipient_list=[recipient],
            attachments=attachments,
            organization=_trusted_notification_organization(signup, signup.student),
        )
        return True
    except Exception as exc:
        logger.error("Outreach reminder dispatch failed (%s).", type(exc).__name__)
        return False


def notify_mentor_outreach_reminder(mentor_signup, signups=None, shifts_by_id=None):
    """Send an outreach shift reminder email to a mentor."""
    recipient = adult_recipient_email(mentor_signup.adult)
    if not recipient:
        return False

    signups = _matching_reminder_signups(
        mentor_signup, signups, "adult", shifts_by_id=shifts_by_id
    )
    context = _base_mentor_signup_context(
        mentor_signup, reminder_signups=signups, shifts_by_id=shifts_by_id
    )
    shift = _reminder_shift(mentor_signup, shifts_by_id)
    event_name = shift.event.name
    subject = f"Reminder: Outreach event {event_name} is tomorrow!"
    attachments = [
        _shift_ics_attachment(
            _reminder_shift(item, shifts_by_id),
            role_label="Mentor Support",
            include_shift_id=len(signups) > 1,
        )
        for item in signups
    ]

    try:
        send_templated_notification(
            subject=subject,
            template_name="outreach/emails/mentor_reminder.html",
            context=context,
            recipient_list=[recipient],
            attachments=attachments,
            organization=_trusted_notification_organization(
                mentor_signup, mentor_signup.adult
            ),
        )
        return True
    except Exception as exc:
        logger.error(
            "Outreach mentor reminder dispatch failed (%s).", type(exc).__name__
        )
        return False


def notify_student_event_deleted(signup):
    """Send outreach event cancellation email to a student (champion or helper)."""
    recipient = student_recipient_email(signup.student)
    if not recipient:
        return False

    context = _base_signup_context(signup)
    event_name = signup.shift.event.name
    subject = f"Cancelled: Outreach event {event_name}"

    try:
        send_templated_notification(
            subject=subject,
            template_name="outreach/emails/event_cancelled.html",
            context=context,
            recipient_list=[recipient],
            organization=_trusted_notification_organization(signup, signup.student),
        )
        return True
    except Exception as exc:
        logger.error(
            "Outreach event cancellation dispatch failed (%s).", type(exc).__name__
        )
        return False


def notify_mentor_event_deleted(mentor_signup):
    """Send outreach event cancellation email to a mentor."""
    recipient = adult_recipient_email(mentor_signup.adult)
    if not recipient:
        return False

    context = _base_mentor_signup_context(mentor_signup)
    event_name = mentor_signup.shift.event.name
    subject = f"Cancelled: Outreach event {event_name}"

    try:
        send_templated_notification(
            subject=subject,
            template_name="outreach/emails/mentor_event_cancelled.html",
            context=context,
            recipient_list=[recipient],
            organization=_trusted_notification_organization(
                mentor_signup, mentor_signup.adult
            ),
        )
        return True
    except Exception as exc:
        logger.error(
            "Outreach mentor event cancellation dispatch failed (%s).",
            type(exc).__name__,
        )
        return False


def notify_outreach_event_deleted(event):
    """Notify all champions, helpers, and mentors when an outreach event is deleted."""
    if not event:
        return 0

    shifts = list(
        event.shifts.all().prefetch_related(
            "signups__student__user",
            "mentor_signups__adult__user",
        )
    )
    sent_count = 0
    notified_students = set()
    notified_mentors = set()

    for shift in shifts:
        for signup in shift.signups.all():
            student = signup.student
            if not student or student.pk in notified_students:
                continue
            if notify_student_event_deleted(signup):
                notified_students.add(student.pk)
                sent_count += 1

        for mentor_signup in shift.mentor_signups.all():
            adult = mentor_signup.adult
            if not adult or adult.pk in notified_mentors:
                continue
            if notify_mentor_event_deleted(mentor_signup):
                notified_mentors.add(adult.pk)
                sent_count += 1

    return sent_count


notify_outreach_event_cancelled = notify_outreach_event_deleted
