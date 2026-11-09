"""toSend Webhook Processing and Signature Verification."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac

from programs.models import (
    EmailDeliveryEvent,
    EmailDeliveryQuarantine,
    TrustedOutgoingEmailRecipient,
)

logger = logging.getLogger(__name__)


def _email_fingerprint(email_address: str) -> str:
    normalized = email_address.strip().casefold()
    return salted_hmac(
        "programs.outgoing_email_recipient", normalized, algorithm="sha256"
    ).hexdigest()


def _message_id_fingerprint(message_id: str) -> str:
    return salted_hmac(
        "programs.outgoing_email_message", message_id, algorithm="sha256"
    ).hexdigest()


def record_trusted_outgoing_message(organization_id, message_id, recipients):
    """Persist provider-confirmed message ownership only after the send transaction commits."""
    if not organization_id or not isinstance(message_id, str):
        return
    message_id = message_id.strip()
    if not message_id or len(message_id) > 255:
        return

    unique_recipients = {}
    for recipient in recipients:
        if not isinstance(recipient, str):
            continue
        address = recipient.strip()
        if address:
            unique_recipients.setdefault(_email_fingerprint(address), address)
    if len(unique_recipients) != 1:
        return

    def persist_mapping():
        try:
            with transaction.atomic():
                mappings = TrustedOutgoingEmailRecipient.objects
                existing = mappings.filter(provider_message_id=message_id)
                existing_orgs = set(existing.values_list("organization_id", flat=True))
                existing_fingerprints = set(
                    existing.filter(organization_id=organization_id).values_list(
                        "recipient_fingerprint", flat=True
                    )
                )
                incoming_fingerprints = set(unique_recipients)
                ambiguous = bool(
                    (existing_orgs and existing_orgs != {organization_id})
                    or (
                        existing_fingerprints
                        and existing_fingerprints != incoming_fingerprints
                    )
                )
                if ambiguous:
                    existing.update(is_ambiguous=True)

                for recipient_fingerprint, recipient_email in unique_recipients.items():
                    mapping, _ = mappings.get_or_create(
                        organization_id=organization_id,
                        provider_message_id=message_id,
                        recipient_fingerprint=recipient_fingerprint,
                        defaults={
                            "recipient_email": recipient_email,
                            "is_ambiguous": ambiguous,
                        },
                    )
                    if (
                        mapping.recipient_email.strip().casefold()
                        != recipient_email.casefold()
                    ):
                        ambiguous = True
                        mappings.filter(provider_message_id=message_id).update(
                            is_ambiguous=True
                        )
                if ambiguous:
                    mappings.filter(provider_message_id=message_id).update(
                        is_ambiguous=True
                    )
            if not ambiguous:
                reconcile_quarantined_message(message_id)
        except Exception as exc:
            logger.warning(
                "Trusted delivery mapping persistence failed (%s).",
                type(exc).__name__,
            )

    transaction.on_commit(persist_mapping)


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
    if sig.startswith("sha256="):
        sig = sig[len("sha256=") :]
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


def _quarantine_event(
    reason_code: str,
    diagnostic: dict[str, Any],
    fingerprint=None,
    *,
    message_id="",
    recipient="",
):
    serialized = json.dumps(diagnostic, sort_keys=True, separators=(",", ":"))
    deduplication_source = fingerprint or serialized
    deduplication_key = salted_hmac(
        "programs.email_delivery_quarantine", deduplication_source, algorithm="sha256"
    ).hexdigest()
    quarantine, _ = EmailDeliveryQuarantine.objects.get_or_create(
        deduplication_key=deduplication_key,
        defaults={
            "reason_code": reason_code,
            "encrypted_diagnostic": serialized,
            "message_id_fingerprint": (
                _message_id_fingerprint(message_id) if message_id else ""
            ),
            "recipient_fingerprint": (
                _email_fingerprint(recipient) if recipient else ""
            ),
        },
    )
    return quarantine


def quarantine_malformed_tosend_payload(raw_body: bytes):
    """Retain only a generic encrypted marker for a signed malformed payload."""
    body_fingerprint = salted_hmac(
        "programs.email_delivery_quarantine_raw",
        raw_body,
        algorithm="sha256",
    ).hexdigest()
    return _quarantine_event(
        EmailDeliveryQuarantine.REASON_MALFORMED,
        {"error": "malformed_signed_json"},
        fingerprint=body_fingerprint,
    )


def _first_value(*values):
    return next((value for value in values if value not in (None, "")), "")


def _recipient_candidates(event_dict, inner_data):
    recipient = _first_value(
        inner_data.get("email"),
        inner_data.get("recipient"),
        inner_data.get("to"),
        event_dict.get("email"),
        event_dict.get("recipient"),
        event_dict.get("to"),
    )
    candidates = recipient if isinstance(recipient, list) else [recipient]
    results = []
    seen = set()
    for candidate in candidates[:5]:
        if isinstance(candidate, dict):
            candidate = candidate.get("email", "")
        if not isinstance(candidate, str):
            continue
        candidate = candidate.strip()[:254]
        normalized = candidate.casefold()
        if candidate and normalized not in seen:
            results.append(candidate)
            seen.add(normalized)
    return results


def _find_trusted_mapping(message_id, recipient):
    recipient_fingerprint = _email_fingerprint(recipient)
    mapping_queryset = TrustedOutgoingEmailRecipient.objects.filter(
        provider_message_id=message_id,
        recipient_fingerprint=recipient_fingerprint,
    )
    organization_ids = list(
        TrustedOutgoingEmailRecipient.objects.filter(provider_message_id=message_id)
        .values_list("organization_id", flat=True)
        .distinct()[:2]
    )
    if len(organization_ids) > 1:
        TrustedOutgoingEmailRecipient.objects.filter(
            provider_message_id=message_id
        ).update(is_ambiguous=True)
        return None, EmailDeliveryQuarantine.REASON_AMBIGUOUS

    mappings = list(mapping_queryset.select_related("organization")[:2])
    if not mappings:
        return None, EmailDeliveryQuarantine.REASON_UNMATCHED
    if len(mappings) != 1 or mappings[0].is_ambiguous:
        return None, EmailDeliveryQuarantine.REASON_AMBIGUOUS
    mapping = mappings[0]
    if mapping.recipient_email.strip().casefold() != recipient.strip().casefold():
        return None, EmailDeliveryQuarantine.REASON_AMBIGUOUS
    return mapping, ""


def _send_local_critical_delivery_alert(event_id):
    event = (
        EmailDeliveryEvent.objects.verified()
        .select_related("organization")
        .filter(pk=event_id)
        .first()
    )
    if not event or not event.is_critical:
        return

    from programs.utils.notifications import (
        get_local_lead_mentor_notification_emails,
        send_templated_notification,
    )

    recipients = get_local_lead_mentor_notification_emails(event.organization)
    if not recipients:
        return
    event_description = (
        "A confirmed hard bounce"
        if event.is_hard_bounce
        else "A confirmed spam complaint"
    )
    send_templated_notification(
        "Critical email delivery event requires review",
        "programs/emails/delivery_issue_alert.html",
        {"event_description": event_description},
        recipients,
        organization=event.organization,
        track_delivery=False,
    )


def _record_verified_event_audit(event):
    from audit.events import AuditEvent
    from audit.models import AuditLog

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
            resource_repr=f"Verified email delivery event {event.pk}",
            notes=f"Verified delivery event type: {event.event_type}.",
            outcome=AuditLog.SUCCESS,
        )
    except Exception as exc:
        logger.warning(
            "Email delivery audit record could not be created (%s).",
            type(exc).__name__,
        )


def _create_verified_delivery_event(
    mapping, message_id, recipient, event_type, bounce_type, reason
):
    if mapping.is_ambiguous:
        return None
    recipient = recipient.strip()
    recipient_fingerprint = _email_fingerprint(recipient)
    if mapping.recipient_fingerprint != recipient_fingerprint:
        return None
    if mapping.recipient_email.strip().casefold() != recipient.casefold():
        return None

    event_key_data = json.dumps(
        {
            "message_id": message_id,
            "recipient_fingerprint": recipient_fingerprint,
            "event_type": event_type,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    event_key = salted_hmac(
        "programs.email_delivery_event", event_key_data, algorithm="sha256"
    ).hexdigest()
    event, created = EmailDeliveryEvent.objects.get_or_create(
        webhook_deduplication_key=event_key,
        defaults={
            "recipient_email": recipient,
            "recipient_fingerprint": recipient_fingerprint,
            "event_type": event_type,
            "bounce_type": bounce_type,
            "reason": reason,
            "message_id": message_id,
            "trusted_mapping": mapping,
            "organization_id": mapping.organization_id,
            "is_verified": True,
        },
    )
    if (
        event.trusted_mapping_id != mapping.pk
        or event.organization_id != mapping.organization_id
    ):
        return None
    event.resolve_entities()
    if created:
        _record_verified_event_audit(event)
        if event.is_critical:
            transaction.on_commit(
                lambda event_id=event.pk: _send_local_critical_delivery_alert(event_id)
            )
    return event


def reconcile_quarantined_message(message_id):
    if not isinstance(message_id, str) or not message_id:
        return 0
    quarantined_events = EmailDeliveryQuarantine.objects.filter(
        message_id_fingerprint=_message_id_fingerprint(message_id),
        reconciled_at__isnull=True,
    )
    reconciled_count = 0
    for quarantine in quarantined_events:
        try:
            diagnostic = json.loads(quarantine.encrypted_diagnostic)
        except (TypeError, ValueError):
            continue
        recipients = diagnostic.get("recipients")
        recipient = (
            recipients[0]
            if isinstance(recipients, list) and len(recipients) == 1
            else ""
        )
        stored_message_id = diagnostic.get("message_id")
        event_type = diagnostic.get("event_type")
        if (
            not recipient
            or stored_message_id != message_id
            or event_type not in dict(EmailDeliveryEvent.EVENT_CHOICES)
        ):
            continue
        mapping, _reason = _find_trusted_mapping(message_id, recipient)
        if mapping is None:
            if _reason == EmailDeliveryQuarantine.REASON_AMBIGUOUS:
                quarantine.reason_code = _reason
                quarantine.save(update_fields=["reason_code"])
            continue
        raw_bounce_type = diagnostic.get("bounce_type", "")
        bounce_type = (
            EmailDeliveryEvent.BOUNCE_HARD
            if raw_bounce_type in {"hard", "permanent"}
            else (
                EmailDeliveryEvent.BOUNCE_SOFT
                if raw_bounce_type in {"soft", "transient"}
                else ""
            )
        )
        event = _create_verified_delivery_event(
            mapping,
            message_id,
            recipient,
            event_type,
            bounce_type,
            diagnostic.get("reason", ""),
        )
        if event is None:
            continue
        quarantine.reconciled_at = timezone.now()
        quarantine.save(update_fields=["reconciled_at"])
        reconciled_count += 1
    return reconciled_count


def process_tosend_event(
    event_dict: Any,
) -> EmailDeliveryEvent | EmailDeliveryQuarantine:
    """Correlate a signed provider event or retain its minimized diagnostic."""
    if not isinstance(event_dict, dict):
        return _quarantine_event(
            EmailDeliveryQuarantine.REASON_MALFORMED,
            {"error": "non_object_event"},
        )

    inner_data = (
        event_dict.get("data")
        if isinstance(event_dict.get("data"), dict)
        else event_dict
    )
    raw_event_type = _first_value(
        event_dict.get("event"),
        event_dict.get("type"),
        inner_data.get("event"),
        inner_data.get("type"),
    )
    raw_event_type = (
        raw_event_type.strip().lower()[:80] if isinstance(raw_event_type, str) else ""
    )
    if "bounce" in raw_event_type:
        event_type = EmailDeliveryEvent.EVENT_BOUNCED
    elif (
        "complaint" in raw_event_type
        or "complain" in raw_event_type
        or "spam" in raw_event_type
    ):
        event_type = EmailDeliveryEvent.EVENT_COMPLAINT
    elif "unsubscribe" in raw_event_type:
        event_type = EmailDeliveryEvent.EVENT_UNSUBSCRIBED
    elif "deliver" in raw_event_type:
        event_type = EmailDeliveryEvent.EVENT_DELIVERED
    else:
        event_type = "unknown"

    recipients = _recipient_candidates(event_dict, inner_data)
    message_id = _first_value(
        inner_data.get("message_id"), event_dict.get("message_id")
    )
    message_id = message_id.strip()[:255] if isinstance(message_id, str) else ""
    raw_bounce_type = _first_value(
        inner_data.get("bounce_type"),
        inner_data.get("classification"),
        event_dict.get("bounce_type"),
    )
    raw_bounce_type = (
        raw_bounce_type.strip().lower()[:40] if isinstance(raw_bounce_type, str) else ""
    )
    raw_reason = _first_value(
        inner_data.get("reason"),
        inner_data.get("error"),
        inner_data.get("smtp_response"),
        inner_data.get("message"),
        event_dict.get("reason"),
    )
    reason = raw_reason.strip()[:500] if isinstance(raw_reason, str) else ""

    if len(recipients) > 1:
        reason_code = EmailDeliveryQuarantine.REASON_AMBIGUOUS
    elif not recipients or not message_id or event_type == "unknown":
        reason_code = EmailDeliveryQuarantine.REASON_MALFORMED
    else:
        reason_code = EmailDeliveryQuarantine.REASON_UNMATCHED

    diagnostic = {
        "event_type": event_type,
        "recipients": recipients,
        "message_id": message_id,
        "bounce_type": raw_bounce_type,
        "reason": reason,
    }
    fingerprint_data = {
        "event_type": event_type,
        "recipients": recipients,
        "message_id": message_id,
    }
    fingerprint = json.dumps(fingerprint_data, sort_keys=True, separators=(",", ":"))
    if reason_code == EmailDeliveryQuarantine.REASON_UNMATCHED:
        mapping, mapping_reason = _find_trusted_mapping(message_id, recipients[0])
        if mapping is not None:
            raw_bounce_type = raw_bounce_type or ""
            is_hard_bounce = inner_data.get("is_hard_bounce") is True
            bounce_type = (
                EmailDeliveryEvent.BOUNCE_HARD
                if is_hard_bounce or raw_bounce_type in {"hard", "permanent"}
                else (
                    EmailDeliveryEvent.BOUNCE_SOFT
                    if inner_data.get("is_hard_bounce") is False
                    or raw_bounce_type in {"soft", "transient"}
                    else ""
                )
            )
            verified_event = _create_verified_delivery_event(
                mapping,
                message_id,
                recipients[0],
                event_type,
                bounce_type,
                reason,
            )
            if verified_event is not None:
                return verified_event
        elif mapping_reason == EmailDeliveryQuarantine.REASON_AMBIGUOUS:
            reason_code = mapping_reason

    quarantine = _quarantine_event(
        reason_code,
        diagnostic,
        fingerprint=fingerprint,
        message_id=message_id,
        recipient=recipients[0] if len(recipients) == 1 else "",
    )
    if message_id:
        transaction.on_commit(
            lambda stored_message_id=message_id: reconcile_quarantined_message(
                stored_message_id
            )
        )
    return quarantine
