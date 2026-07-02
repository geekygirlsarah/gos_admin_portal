import json
import logging
import re

from django.conf import settings
from django.db import models

from .events import AuditEvent

logger = logging.getLogger("audit")


class AuditLog(models.Model):
    """
    Immutable, append-only audit log.

    Rules:
      - Never UPDATE or DELETE rows — enforced by overriding save() and delete().
      - Only lead mentors (or superusers) may view records.
      - Every save() mirrors the record to the Python 'audit' logger so it
        also appears on stdout/stderr via the configured log handlers.
    """

    # 1. Timestamp — auto_now_add gives UTC; DateTimeField stores microsecond precision.
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)

    # 2. Actor identity — FK by user ID; NULL for system/anonymous actions.
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_actions",
        db_index=True,
        help_text="The user who performed the action (by PK). NULL = system or anonymous.",
    )

    # 3. Standardised event code.
    event = models.CharField(
        max_length=50,
        choices=AuditEvent.choices,
        db_index=True,
    )

    # 4. Affected resource.
    resource_type = models.CharField(
        max_length=100,
        db_index=True,
        help_text="Model name of the affected resource, e.g. 'Student', 'User'.",
    )
    resource_id = models.CharField(
        max_length=255,
        db_index=True,
        help_text="PK of the affected record (stored as string to support UUIDs).",
    )
    resource_repr = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Human-readable label of the resource at the time of the event.",
    )

    # 5. Before / after values.
    before = models.JSONField(
        null=True,
        blank=True,
        help_text="Field values before the change. NULL for creation events.",
    )
    after = models.JSONField(
        null=True,
        blank=True,
        help_text="Field values after the change. NULL for deletion events.",
    )

    # 6. Request metadata.
    ip_address = models.GenericIPAddressField(
        null=True,
        blank=True,
        help_text="Client IP address that triggered the action.",
    )
    session_id = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Django session key at the time of the action.",
    )

    # 7. Outcome.
    SUCCESS = "success"
    FAILURE = "failure"
    OUTCOME_CHOICES = [
        (SUCCESS, "Success"),
        (FAILURE, "Failure"),
    ]
    outcome = models.CharField(
        max_length=10,
        choices=OUTCOME_CHOICES,
        default=SUCCESS,
        db_index=True,
    )

    # Optional freeform note (e.g. failure reason, extra context).
    notes = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-timestamp"]
        indexes = [
            models.Index(
                fields=["resource_type", "resource_id"], name="audit_resource_idx"
            ),
            models.Index(fields=["actor", "timestamp"], name="audit_actor_ts_idx"),
            models.Index(fields=["event", "timestamp"], name="audit_event_ts_idx"),
        ]
        # Suppress add/change/delete permissions — view only.
        default_permissions = ("view",)

    def __str__(self):
        actor_label = self.actor_id or "system"
        return (
            f"[{self.timestamp:%Y-%m-%d %H:%M:%S}] "
            f"{self.event} by {actor_label} → "
            f"{self.resource_type}#{self.resource_id}"
        )

    # ------------------------------------------------------------------
    # Immutability guards
    # ------------------------------------------------------------------

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError("AuditLog records are immutable and cannot be updated.")
        super().save(*args, **kwargs)
        self._emit_to_logger()

    def delete(self, *args, **kwargs):
        raise ValueError("AuditLog records are immutable and cannot be deleted.")

    # ------------------------------------------------------------------
    # Stdout / stderr mirroring
    # ------------------------------------------------------------------

    _SENSITIVE_KEY_RE = re.compile(
        r"(pass(word)?|passwd|pwd|secret|token|api[_-]?key|authorization|session|cookie)",
        re.IGNORECASE,
    )

    @classmethod
    def _is_sensitive_key(cls, key: str) -> bool:
        return bool(cls._SENSITIVE_KEY_RE.search(str(key)))

    @classmethod
    def _redact_value(cls, value, parent_key: str | None = None):
        if parent_key is not None and cls._is_sensitive_key(parent_key):
            return "***REDACTED***"

        if isinstance(value, dict):
            return {
                k: cls._redact_value(v, parent_key=str(k)) for k, v in value.items()
            }

        if isinstance(value, list):
            return [cls._redact_value(item, parent_key=parent_key) for item in value]

        if isinstance(value, tuple):
            return tuple(
                cls._redact_value(item, parent_key=parent_key) for item in value
            )

        return value

    def _emit_to_logger(self):
        payload = {
            "audit": True,
            "timestamp": self.timestamp.isoformat(),
            "event": self.event,
            "actor_id": self.actor_id,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "resource_repr": self.resource_repr,
            "ip_address": str(self.ip_address or ""),
            "session_id": "***REDACTED***" if self.session_id else "",
            "outcome": self.outcome,
            "before": self._redact_value(self.before),
            "after": self._redact_value(self.after),
            "notes": self._redact_value(self.notes, parent_key="notes"),
        }
        level = logging.INFO if self.outcome == self.SUCCESS else logging.WARNING
        logger.log(level, json.dumps(payload))
