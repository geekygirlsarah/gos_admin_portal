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
        or student.andrew_email
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
        or adult.andrew_email
        or (adult.user.email if getattr(adult, "user", None) else None)
    )
    if email and email.strip():
        return email.strip()
    return None


def outreach_link(event):
    """Return the absolute URL to the outreach events list for ``event``."""
    if event and event.program_id:
        path = reverse("outreach:event_list", kwargs={"program_id": event.program_id})
        org = getattr(event, "organization", None) or getattr(
            getattr(event, "program", None), "organization", None
        )
        base = get_organization_email_settings(org).get("portal_url", "").rstrip("/")
        return f"{base}{path}" if base else path
    return None


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


def _shift_ics_attachment(shift, role_label=None):
    ics_content = generate_shift_ics(shift, role_label=role_label)
    filename = f"{slugify(shift.event.name or 'outreach-event')}.ics"
    return (filename, ics_content, "text/calendar")


def _shift_participants(shift):
    """Return lists of display names for participants on this shift."""
    from outreach.models import OutreachSignup

    champions = [
        signup.student.display_name
        for signup in shift.signups.filter(role=OutreachSignup.CHAMPION).select_related(
            "student"
        )
    ]
    helpers = [
        signup.student.display_name
        for signup in shift.signups.filter(role=OutreachSignup.HELPER).select_related(
            "student"
        )
    ]
    mentors = [
        ms.adult.display_name for ms in shift.mentor_signups.select_related("adult")
    ]
    return {
        "champions": champions,
        "helpers": helpers,
        "mentors": mentors,
    }


def _base_signup_context(signup):
    shift = signup.shift
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
    ctx.update(_shift_participants(shift))
    return ctx


def _base_mentor_signup_context(mentor_signup):
    shift = mentor_signup.shift
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
    ctx.update(_shift_participants(shift))
    return ctx


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
            organization=getattr(signup.shift.event, "organization", None),
        )
        return True
    except Exception:
        logger.exception("Failed to send outreach signup confirmation to %s", recipient)
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
            organization=getattr(mentor_signup.shift.event, "organization", None),
        )
        return True
    except Exception:
        logger.exception(
            "Failed to send outreach mentor signup confirmation to %s", recipient
        )
        return False


def notify_outreach_reminder(signup):
    """Send outreach shift reminder email to a student."""
    recipient = student_recipient_email(signup.student)
    if not recipient:
        return False

    context = _base_signup_context(signup)
    event_name = signup.shift.event.name
    subject = f"Reminder: Outreach event {event_name} is tomorrow!"
    attachment = _shift_ics_attachment(
        signup.shift, role_label=signup.get_role_display()
    )

    try:
        send_templated_notification(
            subject=subject,
            template_name="outreach/emails/reminder.html",
            context=context,
            recipient_list=[recipient],
            attachments=[attachment],
            organization=getattr(signup.shift.event, "organization", None),
        )
        return True
    except Exception:
        logger.exception("Failed to send outreach reminder to %s", recipient)
        return False


def notify_mentor_outreach_reminder(mentor_signup):
    """Send outreach shift reminder email to a mentor."""
    recipient = adult_recipient_email(mentor_signup.adult)
    if not recipient:
        return False

    context = _base_mentor_signup_context(mentor_signup)
    event_name = mentor_signup.shift.event.name
    subject = f"Reminder: Outreach event {event_name} is tomorrow!"
    attachment = _shift_ics_attachment(mentor_signup.shift, role_label="Mentor Support")

    try:
        send_templated_notification(
            subject=subject,
            template_name="outreach/emails/mentor_reminder.html",
            context=context,
            recipient_list=[recipient],
            attachments=[attachment],
            organization=getattr(mentor_signup.shift.event, "organization", None),
        )
        return True
    except Exception:
        logger.exception("Failed to send outreach mentor reminder to %s", recipient)
        return False
