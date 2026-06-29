"""
Thin service layer for writing AuditLog entries.

Usage
-----
    from audit.service import log_event
    from audit.events import AuditEvent

    log_event(
        request=request,
        event=AuditEvent.ROLE_CHANGED,
        resource=some_user_instance,
        before={"role": "staff"},
        after={"role": "admin"},
    )
"""

from __future__ import annotations

import logging

from .events import AuditEvent
from .models import AuditLog

logger = logging.getLogger("audit")


def _get_ip(request) -> str | None:
    if not request:
        return None
    x_forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded:
        return x_forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def _get_session_id(request) -> str:
    if not request:
        return ""
    try:
        return request.session.session_key or ""
    except AttributeError:
        return ""


def log_event(
    *,
    event: AuditEvent,
    resource,
    request=None,
    actor=None,
    before: dict | None = None,
    after: dict | None = None,
    outcome: str = AuditLog.SUCCESS,
    notes: str = "",
) -> AuditLog | None:
    """
    Write one immutable AuditLog record and mirror it to the Python logger.

    Parameters
    ----------
    event:    One of the AuditEvent choices.
    resource: The Django model instance being acted on.
    request:  The current HttpRequest (resolves IP, session, and actor).
              Pass None for management commands or background tasks.
    actor:    Explicit actor override (defaults to request.user when omitted).
    before:   Dict of field values before the change.
    after:    Dict of field values after the change.
    outcome:  AuditLog.SUCCESS (default) or AuditLog.FAILURE.
    notes:    Optional freeform context string.

    Returns
    -------
    The saved AuditLog instance, or None if an internal error occurred.
    Audit failures are deliberately swallowed so they never crash the
    primary operation.
    """
    try:
        resolved_actor = actor
        if resolved_actor is None and request is not None:
            user = getattr(request, "user", None)
            if user is not None and user.is_authenticated:
                resolved_actor = user

        entry = AuditLog(
            actor=resolved_actor,
            event=event,
            resource_type=type(resource).__name__,
            resource_id=str(resource.pk),
            resource_repr=str(resource),
            before=before,
            after=after,
            ip_address=_get_ip(request),
            session_id=_get_session_id(request),
            outcome=outcome,
            notes=notes,
        )
        entry.save()
        return entry

    except Exception:
        logger.exception("Failed to write audit log entry for event=%s", event)
        return None
