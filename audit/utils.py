"""
Unified audit query helpers.

Provides a single query surface over AuditLog records and correlates
session/IP data for abuse detection.
"""

from __future__ import annotations

from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone

from .models import AuditLog


def get_actor_timeline(actor, hours: int = 24):
    """Return AuditLog entries for *actor* within the last *hours* hours."""
    since = timezone.now() - timedelta(hours=hours)
    return AuditLog.objects.filter(actor=actor, timestamp__gte=since).order_by(
        "timestamp"
    )


def detect_session_anomalies(hours: int = 24):
    """Find session IDs used from more than one distinct IP address.

    Returns a queryset of dicts with ``session_id``, ``ip_count``,
    ``ip_addresses``, and ``actor_ids``.
    """
    since = timezone.now() - timedelta(hours=hours)
    return (
        AuditLog.objects.filter(
            session_id__isnull=False,
            session_id__gt="",
            timestamp__gte=since,
        )
        .values("session_id")
        .annotate(
            ip_count=Count("ip_address", distinct=True),
            distinct_ips=Count("ip_address", distinct=True),
        )
        .filter(ip_count__gt=1)
        .order_by("-ip_count")
    )


def get_login_failure_summary(hours: int = 24, threshold: int = 5):
    """Summarize login failures by IP address.

    Returns entries with ``ip_address`` and ``failure_count`` for IPs
    exceeding *threshold* failures.
    """
    since = timezone.now() - timedelta(hours=hours)
    return (
        AuditLog.objects.filter(
            event=AuditEvent.LOGIN_FAILED,
            timestamp__gte=since,
        )
        .values("ip_address")
        .annotate(failure_count=Count("id"))
        .filter(failure_count__gte=threshold)
        .order_by("-failure_count")
    )


def get_sensitive_data_access_outliers(hours: int = 24, threshold: int = 30):
    """Find actors viewing an unusual number of distinct records.

    Returns entries with ``actor_id`` and ``records_viewed`` for actors
    exceeding *threshold* distinct resource views.
    """
    since = timezone.now() - timedelta(hours=hours)
    return (
        AuditLog.objects.filter(
            event=AuditEvent.SENSITIVE_DATA_VIEW,
            timestamp__gte=since,
        )
        .values("actor_id")
        .annotate(records_viewed=Count("resource_id", distinct=True))
        .filter(records_viewed__gte=threshold)
        .order_by("-records_viewed")
    )


def get_privilege_changes(hours: int = 24):
    """Return all privilege-related events in the window."""
    since = timezone.now() - timedelta(hours=hours)
    return AuditLog.objects.filter(
        event__in=[
            AuditEvent.ROLE_CHANGED,
            AuditEvent.PASSWORD_RESET,
            AuditEvent.ACCOUNT_DEACTIVATED,
        ],
        timestamp__gte=since,
    ).order_by("-timestamp")


def get_guardian_removals(hours: int = 24):
    """Return all guardian removal events in the window."""
    since = timezone.now() - timedelta(hours=hours)
    return AuditLog.objects.filter(
        event=AuditEvent.GUARDIAN_REMOVED,
        timestamp__gte=since,
    ).order_by("-timestamp")


# Re-export AuditEvent for callers that import from utils
from .events import AuditEvent  # noqa: E402, F401
