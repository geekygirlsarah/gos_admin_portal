"""
audit_digest -- daily abuse-detection digest for the GoS Admin Portal.

Runs a set of pre-built queries against AuditLog and prints a summary of
potential anomalies: login failures, sensitive-data access outliers,
privilege changes, and guardian removals.

Usage::

    python manage.py audit_digest                  # last 24 hours, default thresholds
    python manage.py audit_digest --days 7         # last 7 days
    python manage.py audit_digest --threshold 30   # custom threshold for login failures
    python manage.py audit_digest --csv report.csv # write CSV alongside stdout
"""

from __future__ import annotations

import csv
import sys
from io import StringIO

from django.core.management.base import BaseCommand
from django.db.models import Count
from django.utils import timezone

from audit.events import AuditEvent
from audit.models import AuditLog


class Command(BaseCommand):
    help = (
        "Generate an abuse-detection digest from AuditLog records. "
        "Flags login anomalies, data-access outliers, privilege changes, "
        "and guardian removals within the requested window."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=1,
            help="Look back this many days (default: 1).",
        )
        parser.add_argument(
            "--threshold",
            type=int,
            default=20,
            help="Minimum count to flag login failures or data views (default: 20).",
        )
        parser.add_argument(
            "--csv",
            type=str,
            default="",
            help="Optional file path to write a CSV of all flagged events.",
        )

    def handle(self, *args, **options):
        days = options["days"]
        threshold = options["threshold"]
        csv_path = options.get("csv", "")
        since = timezone.now() - __import__("datetime").timedelta(days=days)

        self.stdout.write(self.style.SUCCESS(f"Audit Digest -- last {days} day(s)\n"))
        self.stdout.write("=" * 60 + "\n")

        all_flagged = []

        # --- 1. Login failures by IP ---
        self.stdout.write(f"\n[1] LOGIN FAILURES (threshold >= {threshold})")
        self.stdout.write("-" * 60)
        login_failures = (
            AuditLog.objects.filter(
                event=AuditEvent.LOGIN_FAILED,
                timestamp__gte=since,
            )
            .values("ip_address")
            .annotate(failure_count=Count("id"))
            .filter(failure_count__gte=threshold)
            .order_by("-failure_count")
        )
        if login_failures:
            for row in login_failures:
                ip = row["ip_address"] or "unknown"
                count = row["failure_count"]
                self.stdout.write(self.style.WARNING(f"  {ip}: {count} failures"))
                all_flagged.append(
                    {"category": "login_failure", "ip": ip, "count": count}
                )
        else:
            self.stdout.write("  No IPs exceeded threshold.\n")

        # --- 2. Sensitive data access outliers ---
        self.stdout.write(f"\n[2] SENSITIVE DATA ACCESS (threshold >= {threshold})")
        self.stdout.write("-" * 60)
        data_views = (
            AuditLog.objects.filter(
                event=AuditEvent.SENSITIVE_DATA_VIEW,
                timestamp__gte=since,
            )
            .values("actor_id")
            .annotate(records_viewed=Count("resource_id", distinct=True))
            .filter(records_viewed__gte=threshold)
            .order_by("-records_viewed")
        )
        if data_views:
            for row in data_views:
                actor_id = row["actor_id"]
                count = row["records_viewed"]
                self.stdout.write(
                    self.style.WARNING(f"  Actor {actor_id}: {count} distinct records")
                )
                all_flagged.append(
                    {"category": "data_access", "actor_id": actor_id, "count": count}
                )
        else:
            self.stdout.write("  No actors exceeded threshold.\n")

        # --- 3. Privilege changes ---
        self.stdout.write("\n[3] PRIVILEGE CHANGES")
        self.stdout.write("-" * 60)
        priv_events = AuditLog.objects.filter(
            event__in=[
                AuditEvent.ROLE_CHANGED,
                AuditEvent.PASSWORD_RESET,
                AuditEvent.ACCOUNT_DEACTIVATED,
            ],
            timestamp__gte=since,
        ).order_by("-timestamp")
        if priv_events:
            for entry in priv_events:
                actor_name = entry.actor.username if entry.actor else "system"
                self.stdout.write(
                    self.style.WARNING(
                        f"  {entry.timestamp:%Y-%m-%d %H:%M} "
                        f"{entry.event} by {actor_name} on {entry.resource_repr}"
                    )
                )
                all_flagged.append(
                    {
                        "category": "privilege_change",
                        "event": entry.event,
                        "actor": actor_name,
                        "resource": entry.resource_repr,
                    }
                )
        else:
            self.stdout.write("  No privilege changes in window.\n")

        # --- 4. Guardian removals ---
        self.stdout.write("\n[4] GUARDIAN REMOVALS")
        self.stdout.write("-" * 60)
        removals = AuditLog.objects.filter(
            event=AuditEvent.GUARDIAN_REMOVED,
            timestamp__gte=since,
        ).order_by("-timestamp")
        if removals:
            for entry in removals:
                self.stdout.write(
                    self.style.WARNING(
                        f"  {entry.timestamp:%Y-%m-%d %H:%M} "
                        f"{entry.resource_repr} ({entry.notes})"
                    )
                )
                all_flagged.append(
                    {
                        "category": "guardian_removal",
                        "resource": entry.resource_repr,
                        "notes": entry.notes,
                    }
                )
        else:
            self.stdout.write("  No guardian removals in window.\n")

        # --- 5. Session anomalies ---
        self.stdout.write("\n[5] SESSION ANOMALIES (multi-IP sessions)")
        self.stdout.write("-" * 60)
        session_anomalies = (
            AuditLog.objects.filter(
                session_id__isnull=False,
                session_id__gt="",
                timestamp__gte=since,
            )
            .values("session_id")
            .annotate(ip_count=Count("ip_address", distinct=True))
            .filter(ip_count__gt=1)
            .order_by("-ip_count")
        )
        if session_anomalies:
            for row in session_anomalies:
                self.stdout.write(
                    self.style.WARNING(
                        f"  Session {row['session_id'][:20]}... "
                        f"used from {row['ip_count']} different IPs"
                    )
                )
                all_flagged.append(
                    {
                        "category": "session_anomaly",
                        "session_id": row["session_id"],
                        "ip_count": row["ip_count"],
                    }
                )
        else:
            self.stdout.write("  No session anomalies detected.\n")

        # --- Summary ---
        self.stdout.write("\n" + "=" * 60)
        total = len(all_flagged)
        if total:
            self.stdout.write(self.style.WARNING(f"Total flagged items: {total}"))
        else:
            self.stdout.write(self.style.SUCCESS("No anomalies detected."))

        # --- CSV export ---
        if csv_path and all_flagged:
            with open(csv_path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=all_flagged[0].keys())
                writer.writeheader()
                writer.writerows(all_flagged)
            self.stdout.write(f"\nCSV written to {csv_path}")
