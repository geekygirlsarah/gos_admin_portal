"""Notification/email sending utilities."""

from __future__ import annotations

import html
import re
import secrets
import string

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, mailers

LEAD_MENTOR_EMAIL = "leads@girlsofsteelrobotics.org"


def generate_otp(length=6):
    return "".join(secrets.choice(string.digits) for _ in range(length))


def send_otp_email(email, otp, organization=None):
    from django.template.loader import render_to_string

    from organizations.utils import get_organization_email_settings

    org_cfg = get_organization_email_settings(organization)
    from_email = (
        org_cfg.get("notification_from_email")
        or getattr(settings, "NOTIFICATION_FROM_EMAIL", None)
        or settings.DEFAULT_FROM_EMAIL
    )
    name = org_cfg.get("sender_display_name") or getattr(
        settings, "DEFAULT_FROM_NAME", None
    )
    if name and "<" not in from_email:
        from_email = f'"{name}" <{from_email}>'

    reply_to = org_cfg.get("reply_to_email") or getattr(
        settings, "DEFAULT_REPLY_TO_EMAIL", None
    )
    reply_to_list = [reply_to] if reply_to else None

    context = {
        "code": otp,
        "org_email_settings": org_cfg,
        "organization": organization,
    }
    subject = render_to_string("account/email/login_code_subject.txt", context).strip()
    subject = "".join(subject.splitlines())
    text_message = render_to_string("account/email/login_code_message.txt", context)
    html_message = render_to_string("account/email/login_code_message.html", context)

    msg = EmailMultiAlternatives(
        subject=subject,
        body=text_message,
        from_email=from_email,
        to=[email],
        reply_to=reply_to_list,
    )
    msg.attach_alternative(html_message, "text/html")
    msg.send()


def get_lead_mentor_notification_email():
    """Email address that should receive Lead Mentor notifications (e.g. new
    sliding scale applications). Configurable via LEAD_MENTOR_NOTIFICATION_EMAIL.
    """
    return getattr(settings, "LEAD_MENTOR_NOTIFICATION_EMAIL", LEAD_MENTOR_EMAIL)


def get_sender_connection(from_account, organization=None):
    """Return an ``(email_backend, from_email, reply_to)`` tuple for the messaging UI.

    ``from_account`` is the ``from_account`` form value: ``"DEFAULT"`` (or an
    unrecognized value) selects the default MAILERS mailer and the default
    from address; any other value is matched against the organization's or
    settings' ``EMAIL_SENDER_ACCOUNTS`` by ``key`` or ``email``.
    """
    from organizations.utils import get_organization_email_settings, get_sender_accounts

    accounts = get_sender_accounts(organization)
    org_cfg = get_organization_email_settings(organization)

    acc = None
    if accounts and from_account and from_account != "DEFAULT":
        for a in accounts:
            if (a.get("key") or a.get("email")) == from_account:
                acc = a
                break

    if acc:
        alias = f"sender_{acc.get('key') or acc.get('email')}"
        raw_email = (
            acc.get("email")
            or org_cfg.get("notification_from_email")
            or settings.DEFAULT_FROM_EMAIL
        )
        display_name = acc.get("display_name")
        from_email = f'"{display_name}" <{raw_email}>' if display_name else raw_email
        reply_to = (
            acc.get("reply_to")
            or raw_email
            or org_cfg.get("reply_to_email")
            or getattr(settings, "DEFAULT_REPLY_TO_EMAIL", None)
        )
    else:
        alias = "default"
        raw_email = (
            org_cfg.get("notification_from_email")
            or getattr(settings, "NOTIFICATION_FROM_EMAIL", None)
            or settings.DEFAULT_FROM_EMAIL
            or "notifications@girlsofsteelrobotics.org"
        )
        name = org_cfg.get("sender_display_name") or getattr(
            settings, "DEFAULT_FROM_NAME", None
        )
        from_email = (
            f'"{name}" <{raw_email}>' if (name and "<" not in raw_email) else raw_email
        )
        reply_to = (
            org_cfg.get("reply_to_email")
            or getattr(settings, "DEFAULT_REPLY_TO_EMAIL", None)
            or raw_email
        )
    try:
        backend_conn = mailers[alias]
    except Exception:
        backend_conn = mailers["default"]
    return backend_conn, from_email, reply_to


def send_templated_notification(
    subject,
    template_name,
    context,
    recipient_list,
    from_email=None,
    reply_to=None,
    organization=None,
):
    """
    Renders an HTML template and sends it as an email with a plain-text fallback
    generated from the HTML content and proper Reply-To headers.
    """
    from django.template.loader import render_to_string
    from django.utils.html import strip_tags

    from organizations.utils import get_organization_email_settings

    org_cfg = get_organization_email_settings(organization)

    if from_email is None:
        from_email = (
            org_cfg.get("notification_from_email")
            or getattr(settings, "NOTIFICATION_FROM_EMAIL", None)
            or settings.DEFAULT_FROM_EMAIL
        )
        name = org_cfg.get("sender_display_name") or getattr(
            settings, "DEFAULT_FROM_NAME", None
        )
        if name and "<" not in from_email:
            from_email = f'"{name}" <{from_email}>'

    if reply_to is None:
        default_reply_to = org_cfg.get("reply_to_email") or getattr(
            settings, "DEFAULT_REPLY_TO_EMAIL", None
        )
        reply_to_list = [default_reply_to] if default_reply_to else None
    elif isinstance(reply_to, str):
        reply_to_list = [reply_to]
    else:
        reply_to_list = list(reply_to)

    ctx = dict(context) if context else {}
    if "org_email_settings" not in ctx:
        ctx["org_email_settings"] = org_cfg
    if "organization" not in ctx:
        if organization is not None:
            ctx["organization"] = organization
        else:
            from organizations.utils import get_default_organization

            ctx["organization"] = get_default_organization()

    html_message = render_to_string(template_name, ctx)
    # Generate a reasonable plain text version from the HTML by stripping tags and unescaping
    plain_message = html.unescape(strip_tags(html_message)).strip()
    # Normalize excessive whitespace/newlines
    plain_message = re.sub(r"\n\s*\n", "\n\n", plain_message)

    for recipient in recipient_list:
        msg = EmailMultiAlternatives(
            subject=subject,
            body=plain_message,
            from_email=from_email,
            to=[recipient],
            reply_to=reply_to_list,
        )
        msg.attach_alternative(html_message, "text/html")
        msg.send()
