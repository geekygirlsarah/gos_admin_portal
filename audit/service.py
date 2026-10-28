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

SENSITIVE_AUDIT_EXACT_KEYS = {
    "password",
    "password_hash",
    "raw_password",
    "user_password",
    "passwords",
    "secret",
    "secret_key",
    "token",
    "otp",
    "auth_token",
    "access_token",
    "api_key",
    "fernet_key",
    "file_encryption_key",
    "encryption_key",
    "allergies",
    "dietary_restrictions",
    "medical_notes",
    "ssn",
    "tax_return",
}


def _is_sensitive_key(key: str) -> bool:
    key_lower = str(key).lower()
    if key_lower in SENSITIVE_AUDIT_EXACT_KEYS:
        return True
    for part in ("password", "secret", "token", "otp", "api_key"):
        if part in key_lower:
            return True
    return False


def sanitize_audit_data(data):
    """Recursively redact sensitive keys in audit dictionary/list structures."""
    if isinstance(data, dict):
        sanitized = {}
        for k, v in data.items():
            if _is_sensitive_key(k):
                sanitized[k] = "[REDACTED]"
            else:
                sanitized[k] = sanitize_audit_data(v)
        return sanitized
    if isinstance(data, list):
        return [sanitize_audit_data(item) for item in data]
    if isinstance(data, tuple):
        return tuple(sanitize_audit_data(item) for item in data)
    return data


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
            before=sanitize_audit_data(before),
            after=sanitize_audit_data(after),
            ip_address=_get_ip(request),
            session_id=_get_session_id(request),
            outcome=outcome,
            notes=notes,
        )
        entry.save()
        return entry

    except Exception as e:
        # Avoid logging exception messages/tracebacks that may include sensitive data.
        # Keep the log informative by including the event and the exception type only.
        # Log a sanitized, constant-form event label to satisfy CodeQL/GHAS.
        safe_event = getattr(event, "name", str(event))
        logger.error(
            "Failed to write audit log entry for event=%s (error=%s)",
            safe_event,
            type(e).__name__,
        )
        return None


def log_deletion(
    *,
    resource,
    request=None,
    actor=None,
    before: dict | None = None,
    notes: str = "",
) -> AuditLog | None:
    """
    Write an immutable RECORD_DELETED audit entry for a record about to be removed.

    Call this immediately BEFORE ``obj.delete()`` (or in ``form_valid`` ahead of
    ``super().form_valid(form)`` for generic ``DeleteView`` subclasses) so the
    snapshot in ``before`` still describes a live row. Failures are swallowed the
    same way as :func:`log_event`.
    """
    return log_event(
        event=AuditEvent.RECORD_DELETED,
        resource=resource,
        request=request,
        actor=actor,
        before=before,
        notes=notes or f"{type(resource).__name__} deleted",
    )
