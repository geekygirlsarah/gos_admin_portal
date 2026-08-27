"""Notification/email sending utilities."""

from __future__ import annotations

import html
import re
import secrets
import string

from django.conf import settings
from django.core.mail import mailers, send_mail

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


def get_sender_connection(from_account):
    """Return an ``(email_backend, from_email)`` pair for the messaging UI.

    ``from_account`` is the ``from_account`` form value: ``"DEFAULT"`` (or an
    unrecognized value) selects the default MAILERS mailer and the default
    from address; any other value is matched against ``EMAIL_SENDER_ACCOUNTS``
    by ``key`` or ``email`` and selects the ``sender_<key>`` mailer configured
    in settings with that account's SMTP credentials.

    The alias scheme here must stay in sync with the ``MAILERS`` entries built
    in ``GoSAdminPortal/settings.py``.
    """
    accounts = getattr(settings, "EMAIL_SENDER_ACCOUNTS", []) or []
    acc = None
    if accounts and from_account and from_account != "DEFAULT":
        for a in accounts:
            if (a.get("key") or a.get("email")) == from_account:
                acc = a
                break

    if acc:
        alias = f"sender_{acc.get('key') or acc.get('email')}"
        from_email = acc.get("email") or settings.DEFAULT_FROM_EMAIL
        display_name = acc.get("display_name")
        if display_name:
            from_email = f'"{display_name}" <{from_email}>'
    else:
        alias = "default"
        from_email = settings.DEFAULT_FROM_EMAIL or "no-reply@example.com"
        name = getattr(settings, "DEFAULT_FROM_NAME", None)
        if name:
            from_email = f'"{name}" <{from_email}>'
    return mailers[alias], from_email


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
