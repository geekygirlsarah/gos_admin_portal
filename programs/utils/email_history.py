"""Encrypted, tenant-scoped records of selected outgoing email messages."""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from email.utils import parseaddr
from typing import Any, Sequence, TypedDict, cast

from django.db import transaction
from django.utils import timezone

from programs.models import (
    EmailHistory,
    EmailHistoryAddress,
    EmailHistoryBatch,
)

logger = logging.getLogger(__name__)

_HISTORY_ATTRIBUTE = "_momentum_email_history"
_HISTORY_ID_ATTRIBUTE = "_momentum_email_history_id"


class _EmailHistoryContext(TypedDict):
    organization_id: Any
    initiated_by_id: Any
    category: str
    retain_body: bool


def _history_context(message) -> _EmailHistoryContext | None:
    context = getattr(message, _HISTORY_ATTRIBUTE, None)
    if not isinstance(context, dict):
        return None
    return cast(_EmailHistoryContext, context)


def mark_email_history(
    message,
    organization,
    *,
    category: str,
    initiated_by=None,
    retain_body: bool = False,
    enabled: bool = True,
) -> None:
    """Attach trusted history context to an email before it is dispatched."""
    if not enabled or organization is None:
        setattr(message, _HISTORY_ATTRIBUTE, None)
        return

    organization_id = getattr(organization, "pk", organization)
    if not organization_id or not category:
        setattr(message, _HISTORY_ATTRIBUTE, None)
        return

    context: _EmailHistoryContext = {
        "organization_id": organization_id,
        "initiated_by_id": getattr(initiated_by, "pk", initiated_by),
        "category": str(category).strip().lower()[:80],
        "retain_body": bool(retain_body),
    }
    setattr(message, _HISTORY_ATTRIBUTE, context)


def _html_body(message) -> str:
    for content, mimetype in getattr(message, "alternatives", ()) or ():
        if mimetype == "text/html":
            return re.sub(r"<img\b[^>]*>", "", str(content), flags=re.IGNORECASE)
    if getattr(message, "content_subtype", None) == "html":
        return re.sub(r"<img\b[^>]*>", "", str(message.body or ""), flags=re.IGNORECASE)
    return ""


def _address_values(message):
    for kind, values in (
        (EmailHistoryAddress.KIND_FROM, [getattr(message, "from_email", "")]),
        (EmailHistoryAddress.KIND_TO, getattr(message, "to", None) or []),
        (EmailHistoryAddress.KIND_CC, getattr(message, "cc", None) or []),
        (EmailHistoryAddress.KIND_BCC, getattr(message, "bcc", None) or []),
    ):
        for value in values:
            if isinstance(value, str) and value.strip():
                yield kind, value.strip()


def queue_email_history(messages: Sequence) -> dict[int, EmailHistory]:
    """Create encrypted queued rows for messages carrying explicit trusted context."""
    grouped = defaultdict(list)
    for message in messages:
        context = _history_context(message)
        if context is not None:
            key = (
                context["organization_id"],
                context["initiated_by_id"],
                context["category"],
                context["retain_body"],
            )
            grouped[key].append(message)

    records = {}
    for (
        organization_id,
        initiated_by_id,
        category,
        retain_body,
    ), grouped_messages in grouped.items():
        try:
            with transaction.atomic():
                batch = EmailHistoryBatch.objects.create(
                    organization_id=organization_id,
                    initiated_by_id=initiated_by_id,
                    category=category,
                )
                for message in grouped_messages:
                    record = EmailHistory.objects.create(
                        organization_id=organization_id,
                        batch=batch,
                        subject=(message.subject or "") if retain_body else None,
                        body_text=(message.body or "") if retain_body else None,
                        body_html=_html_body(message) if retain_body else None,
                        body_retained=retain_body,
                    )
                    for kind, raw_address in _address_values(message):
                        email_address = parseaddr(raw_address)[1].strip()
                        if not email_address:
                            continue
                        from programs.utils.tosend_webhooks import _email_fingerprint

                        EmailHistoryAddress.objects.create(
                            organization_id=organization_id,
                            history=record,
                            kind=kind,
                            email_address=raw_address,
                            email_fingerprint=_email_fingerprint(email_address),
                        )
                    setattr(message, _HISTORY_ID_ATTRIBUTE, record.pk)
                    records[id(message)] = record
        except Exception as exc:
            logger.error(
                "Could not persist outgoing email history (error=%s).",
                type(exc).__name__,
            )
    return records


def _set_status(message, status: str, *, attempted: bool = False) -> None:
    record_id = getattr(message, _HISTORY_ID_ATTRIBUTE, None)
    context = _history_context(message)
    if not record_id or context is None:
        return
    organization_id = context["organization_id"]
    if not organization_id:
        return
    values: dict[str, Any] = {"status": status}
    if attempted:
        values["attempted_at"] = timezone.now()
    if status in (
        EmailHistory.STATUS_ACCEPTED,
        EmailHistory.STATUS_FAILED,
        EmailHistory.STATUS_UNKNOWN,
    ):
        values["completed_at"] = timezone.now()
    try:
        EmailHistory.objects.for_organization(organization_id).filter(
            pk=record_id, organization_id=organization_id
        ).update(**values)
    except Exception as exc:
        logger.error(
            "Could not update outgoing email history status (error=%s).",
            type(exc).__name__,
        )


def mark_email_history_attempted(messages: Sequence) -> None:
    for message in messages:
        _set_status(message, EmailHistory.STATUS_ATTEMPTED, attempted=True)


def complete_email_history(
    messages: Sequence, sent_count: int, error: Exception | None = None
) -> None:
    tracked_count = sum(
        1 for message in messages if getattr(message, _HISTORY_ID_ATTRIBUTE, None)
    )
    accepted = error is None and tracked_count == len(messages) == sent_count
    status = EmailHistory.STATUS_ACCEPTED if accepted else EmailHistory.STATUS_UNKNOWN
    for message in messages:
        _set_status(message, status)


def send_messages_with_history(messages: Sequence) -> int:
    """Send synchronously while recording each known or ambiguous outcome."""
    queue_email_history(messages)
    mark_email_history_attempted(messages)
    sent_count = 0
    for index, message in enumerate(messages):
        try:
            result = message.send()
        except Exception:
            for pending_message in messages[index:]:
                _set_status(pending_message, EmailHistory.STATUS_UNKNOWN)
            raise
        if result:
            sent_count += result
            status = (
                EmailHistory.STATUS_ACCEPTED
                if result == 1
                else EmailHistory.STATUS_UNKNOWN
            )
        else:
            status = EmailHistory.STATUS_UNKNOWN
        _set_status(message, status)
    return sent_count
