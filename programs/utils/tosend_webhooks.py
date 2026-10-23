"""toSend Webhook Processing and Signature Verification."""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.html import strip_tags

from audit.events import AuditEvent
from audit.models import AuditLog
from programs.models import EmailDeliveryEvent
from programs.utils.notifications import get_lead_mentor_notification_email

logger = logging.getLogger(__name__)


def verify_tosend_signature(
    raw_body: bytes,
    signature_header: str | None,
    timestamp_header: str | None = None,
    secret: str | None = None,
) -> bool:
    """Verify HMAC-SHA256 signature for incoming toSend webhook payloads."""
    configured_secret = (
        secret if secret is not None else getattr(settings, "TOSEND_WEBHOOK_SECRET", "")
    )
    if not configured_secret:
        if not getattr(settings, "DEBUG", False) and not getattr(
            settings, "TESTING", False
        ):
            logger.warning(
                "TOSEND_WEBHOOK_SECRET is not configured; rejecting webhook request."
            )
            return False
        # If no secret is configured in dev/testing, allow requests that don't supply a signature
        if not signature_header:
            return True

    if not signature_header:
        return False

    ts = timestamp_header
    sig = signature_header

    # Handle format "t=...,v1=..."
    if "t=" in signature_header:
        parts: dict[str, str] = {}
        for item in signature_header.split(","):
            if "=" in item:
                k, v = item.split("=", 1)
                parts[k.strip()] = v.strip()
        ts = parts.get("t") or ts
        sig = parts.get("v1") or parts.get("v0") or sig

    if not sig:
        return False

    sig = sig.strip()
    secret_bytes = configured_secret.encode("utf-8")

    # Verify with timestamp if present
    if ts:
        expected_dot = hmac.new(
            secret_bytes, f"{ts}.".encode("utf-8") + raw_body, hashlib.sha256
        ).hexdigest()
        expected_nodot = hmac.new(
            secret_bytes, str(ts).encode("utf-8") + raw_body, hashlib.sha256
        ).hexdigest()
        if hmac.compare_digest(sig, expected_dot) or hmac.compare_digest(
            sig, expected_nodot
        ):
            return True

    # Fallback to direct body hash
    expected_direct = hmac.new(secret_bytes, raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected_direct)


def notify_lead_mentors_of_delivery_issue(event: EmailDeliveryEvent) -> None:
    """Send an immediate email notification to Lead Mentors for critical delivery issues."""
    lead_email = get_lead_mentor_notification_email()
    if not lead_email:
        return

    person_label = "Unassigned / External Recipient"
    if event.student:
        person_label = f"Student: {event.student.display_name}"
    elif event.adult:
        person_label = f"Adult: {event.adult.display_name} ({'Parent' if event.adult.is_parent else 'Mentor/Staff'})"
    elif event.application:
        person_label = f"Applicant ({event.application.program.name if event.application.program else 'Application'})"

    subject = f"[GoS Admin Portal] Email Delivery Issue: {event.recipient_email} ({event.get_event_type_display()})"
    if event.is_hard_bounce:
        event_description = "Permanent Hard Bounce (Invalid / Unknown Email Address)"
    elif event.event_type == EmailDeliveryEvent.EVENT_COMPLAINT:
        event_description = "Spam Complaint / Unsubscribe Request"
    else:
        event_description = f"{event.get_event_type_display()} ({event.bounce_type or 'General Delivery Failure'})"

    context = {
        "event": event,
        "person_label": person_label,
        "event_description": event_description,
    }

    try:
        html_message = render_to_string(
            "programs/emails/delivery_issue_alert.html", context
        )
    except Exception:
        html_message = (
            f"<p><strong>Email Delivery Issue Detected</strong></p>"
            f"<ul>"
            f"<li><strong>Recipient:</strong> {event.recipient_email}</li>"
            f"<li><strong>Linked Contact:</strong> {person_label}</li>"
            f"<li><strong>Event Type:</strong> {event_description}</li>"
            f"<li><strong>Reason:</strong> {event.reason or 'No diagnostic message provided'}</li>"
            f"<li><strong>Message ID:</strong> {event.message_id or 'N/A'}</li>"
            f"</ul>"
            f"<p>Please review this contact's profile in the GoS Admin Portal "
            f"to update their email address or follow up by phone.</p>"
        )

    plain_message = strip_tags(html_message)

    try:
        from_email = getattr(
            settings, "DEFAULT_FROM_EMAIL", "notifications@girlsofsteelrobotics.org"
        )
        msg = EmailMultiAlternatives(
            subject=subject,
            body=plain_message,
            from_email=from_email,
            to=[lead_email],
        )
        msg.attach_alternative(html_message, "text/html")
        msg.send()
    except Exception as exc:
        logger.error(
            "Failed to send lead mentor delivery issue alert: %s", exc, exc_info=True
        )


def process_tosend_event(event_dict: dict[str, Any]) -> EmailDeliveryEvent | None:
    """Ingest and record a single toSend delivery event payload."""
    if not isinstance(event_dict, dict):
        return None

    # Handle wrapped payload e.g. {"data": {...}, "type": "email.bounced"}
    inner_data = (
        event_dict.get("data")
        if isinstance(event_dict.get("data"), dict)
        else event_dict
    )

    raw_event_type = str(
        event_dict.get("event")
        or event_dict.get("type")
        or inner_data.get("event")
        or inner_data.get("type")
        or ""
    ).lower()

    recipient = (
        inner_data.get("email")
        or inner_data.get("recipient")
        or inner_data.get("to")
        or event_dict.get("email")
        or event_dict.get("recipient")
        or ""
    )
    if isinstance(recipient, dict):
        recipient = recipient.get("email", "")
    elif isinstance(recipient, list) and recipient:
        first = recipient[0]
        recipient = first.get("email", "") if isinstance(first, dict) else str(first)

    recipient = str(recipient).strip()
    if not recipient:
        return None

    # Normalize event_type
    if "bounce" in raw_event_type:
        event_type = EmailDeliveryEvent.EVENT_BOUNCED
    elif "complaint" in raw_event_type or "spam" in raw_event_type:
        event_type = EmailDeliveryEvent.EVENT_COMPLAINT
    elif "unsubscribe" in raw_event_type:
        event_type = EmailDeliveryEvent.EVENT_UNSUBSCRIBED
    elif "deliver" in raw_event_type:
        event_type = EmailDeliveryEvent.EVENT_DELIVERED
    else:
        event_type = EmailDeliveryEvent.EVENT_BOUNCED

    # Normalize bounce_type
    raw_bounce_type = str(
        inner_data.get("bounce_type")
        or inner_data.get("classification")
        or event_dict.get("bounce_type")
        or ""
    ).lower()
    is_hard = (
        inner_data.get("is_hard_bounce") is True
        or event_dict.get("is_hard_bounce") is True
        or "hard" in raw_bounce_type
        or "permanent" in raw_bounce_type
    )
    is_soft = (
        inner_data.get("is_hard_bounce") is False
        or event_dict.get("is_hard_bounce") is False
        or "soft" in raw_bounce_type
        or "transient" in raw_bounce_type
    )

    if is_hard:
        bounce_type = EmailDeliveryEvent.BOUNCE_HARD
    elif is_soft:
        bounce_type = EmailDeliveryEvent.BOUNCE_SOFT
    else:
        bounce_type = (
            EmailDeliveryEvent.BOUNCE_HARD
            if event_type == EmailDeliveryEvent.EVENT_BOUNCED
            else ""
        )

    reason = str(
        inner_data.get("reason")
        or inner_data.get("error")
        or inner_data.get("smtp_response")
        or inner_data.get("message")
        or event_dict.get("reason")
        or ""
    ).strip()

    message_id = str(
        inner_data.get("message_id")
        or inner_data.get("id")
        or event_dict.get("message_id")
        or ""
    ).strip()

    # Idempotency check: if we already recorded this exact message and event type, return it
    if message_id:
        existing = EmailDeliveryEvent.objects.filter(
            message_id=message_id,
            event_type=event_type,
            recipient_email__iexact=recipient,
        ).first()
        if existing:
            return existing

    event = EmailDeliveryEvent.objects.create(
        recipient_email=recipient,
        event_type=event_type,
        bounce_type=bounce_type,
        reason=reason,
        message_id=message_id,
        payload=event_dict,
    )
    event.resolve_entities()

    # Record in AuditLog
    audit_event = (
        AuditEvent.EMAIL_COMPLAINT
        if event.event_type == EmailDeliveryEvent.EVENT_COMPLAINT
        else AuditEvent.EMAIL_DELIVERY_FAILED
    )
    try:
        AuditLog.objects.create(
            event=audit_event,
            resource_type="EmailDeliveryEvent",
            resource_id=str(event.pk),
            resource_repr=str(event),
            notes=f"Delivery event for {event.recipient_email}: {event.reason or event.get_event_type_display()}",
            outcome=AuditLog.SUCCESS,
        )
    except Exception as exc:
        logger.warning("Failed to create AuditLog for EmailDeliveryEvent: %s", exc)

    # Trigger Lead Mentor notification if critical
    if event.is_critical:
        notify_lead_mentors_of_delivery_issue(event)

    return event
