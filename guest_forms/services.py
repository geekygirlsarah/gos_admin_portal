"""Email services for guest form submissions."""

from __future__ import annotations

import logging
import threading
from typing import List, Optional

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import close_old_connections
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)


def _from_email() -> str:
    email = getattr(settings, "DEFAULT_FROM_EMAIL", "") or "noreply@example.com"
    name = getattr(settings, "DEFAULT_FROM_NAME", None)
    if name:
        return f'"{name}" <{email}>'
    return email


def _should_send_async() -> bool:
    """Backgrounding emails is a workaround for low-CPU environments (Render free tier).
    In tests, we want synchronous delivery to avoid race conditions in assertions.
    """

    if (
        settings.MAILERS.get("default", {}).get("BACKEND", "")
        == "django.core.mail.backends.locmem.EmailBackend"
    ):
        return False
    return getattr(settings, "EMAIL_ASYNC", True)


def _send_html_email(
    subject: str,
    text_body: str,
    html_body: Optional[str],
    recipients: List[str],
) -> None:
    if not recipients:
        return

    def _do_send(close_connections: bool = False):
        msg = EmailMultiAlternatives(
            subject=subject,
            body=text_body,
            from_email=_from_email(),
            to=recipients,
        )
        if html_body:
            msg.attach_alternative(html_body, "text/html")
        try:
            msg.send()
        except Exception:  # pragma: no cover - defensive
            logger.exception("Failed to send email %r to %r", subject, recipients)
        finally:
            if close_connections:
                close_old_connections()

    if _should_send_async():
        threading.Thread(
            target=_do_send,
            kwargs={"close_connections": True},
            name=f"guest-form-email-{subject[:20]}",
        ).start()
    else:
        _do_send()


def send_submission_confirmation_email(submission) -> None:
    """Send a confirmation email to the submitter of a guest form.

    No-ops when the submission has no email address so the public endpoint
    never crashes on missing data.
    """
    if not submission.email:
        logger.warning(
            "Refusing to send guest form confirmation: no email on file "
            "(submission %s)",
            submission.pk,
        )
        return

    ctx = {
        "submission": submission,
        "guest_form": submission.guest_form,
        "participant_name": submission.participant_name,
    }
    subject = f"Your {submission.guest_form.name} was received"
    text_body = render_to_string("guest_forms/email/submission_confirmation.txt", ctx)
    html_body = render_to_string("guest_forms/email/submission_confirmation.html", ctx)
    _send_html_email(
        subject=subject,
        text_body=text_body,
        html_body=html_body,
        recipients=[submission.email],
    )
