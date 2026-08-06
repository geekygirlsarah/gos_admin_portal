"""Notification/email sending utilities."""

from __future__ import annotations

import html
import re
import secrets
import string

from django.conf import settings
from django.core.mail import send_mail

LEAD_MENTOR_EMAIL = "leads@girlsofsteelrobotics.org"


def generate_otp(length=6):
    return "".join(secrets.choice(string.digits) for _ in range(length))


def send_otp_email(email, otp):
    subject = "Your GoS Admin Portal Verification Code"
    message = (
        f"Your verification code is: {otp}\n\nThis code will expire in 10 minutes."
    )
    from_email = settings.DEFAULT_FROM_EMAIL
    name = getattr(settings, "DEFAULT_FROM_NAME", None)
    if name:
        from_email = f'"{name}" <{from_email}>'
    send_mail(subject, message, from_email, [email])


def get_lead_mentor_notification_email():
    """Email address that should receive Lead Mentor notifications (e.g. new
    sliding scale applications). Configurable via LEAD_MENTOR_NOTIFICATION_EMAIL.
    """
    return getattr(settings, "LEAD_MENTOR_NOTIFICATION_EMAIL", LEAD_MENTOR_EMAIL)


def send_templated_notification(
    subject, template_name, context, recipient_list, from_email=None
):
    """
    Renders an HTML template and sends it as an email with a plain-text fallback
    generated from the HTML content.
    """
    from django.template.loader import render_to_string
    from django.utils.html import strip_tags

    if from_email is None:
        from_email = settings.DEFAULT_FROM_EMAIL
        name = getattr(settings, "DEFAULT_FROM_NAME", None)
        if name:
            from_email = f'"{name}" <{from_email}>'

    html_message = render_to_string(template_name, context)
    # Generate a reasonable plain text version from the HTML by stripping tags and unescaping
    plain_message = html.unescape(strip_tags(html_message)).strip()
    # Normalize excessive whitespace/newlines
    plain_message = re.sub(r"\n\s*\n", "\n\n", plain_message)

    for recipient in recipient_list:
        send_mail(
            subject,
            plain_message,
            from_email,
            [recipient],
            html_message=html_message,
        )
