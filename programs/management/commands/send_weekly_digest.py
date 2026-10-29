"""Management command to send automated weekly digest emails to students, parents, and mentors.

Usage:
    python manage.py send_weekly_digest
    python manage.py send_weekly_digest --days-ahead 7
    python manage.py send_weekly_digest --role student
    python manage.py send_weekly_digest --dry-run
"""

import datetime
import logging

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.dateparse import parse_date

from programs.services.digest import get_weekly_digest_recipients
from programs.utils.notifications import send_templated_notification

logger = logging.getLogger(__name__)


def _format_ordinal_date(d: datetime.date) -> str:
    """Format a date with ordinal suffix, e.g. 'November 1st'."""
    day = d.day
    if 10 <= (day % 100) <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{d.strftime('%B')} {day}{suffix}"


class Command(BaseCommand):
    help = "Send automated weekly digest emails for upcoming shifts, opportunities, and attendance."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days-ahead",
            type=int,
            default=7,
            help="Number of days ahead to look for shifts and events (default: 7)",
        )
        parser.add_argument(
            "--date",
            type=str,
            default=None,
            help="Explicit as-of date (YYYY-MM-DD) for calculating digest updates",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would be sent without actually sending emails",
        )
        parser.add_argument(
            "--role",
            choices=["all", "student", "parent", "mentor"],
            default="all",
            help="Filter recipients by role (all, student, parent, mentor)",
        )
        parser.add_argument(
            "--email",
            type=str,
            default=None,
            help="Target a single recipient by email address",
        )
        parser.add_argument(
            "--user-id",
            type=int,
            default=None,
            help="Target a single user by User ID",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Send digest even if there is no new activity or commitments (bypasses empty-state suppression)",
        )

    def handle(self, *args, **options):
        days_ahead = options["days_ahead"]
        date_str = options.get("date")
        dry_run = options["dry_run"]
        role = options["role"]
        email_filter = options["email"]
        user_id_filter = options["user_id"]
        force = options["force"]

        target_date = None
        if date_str:
            target_date = parse_date(date_str)
            if not target_date:
                self.stderr.write(
                    self.style.ERROR(
                        f"Invalid date format: {date_str}. Use YYYY-MM-DD."
                    )
                )
                return

        self.stdout.write(
            f"Collecting weekly digest data (role={role}, days_ahead={days_ahead}, reference_date={target_date})..."
        )

        recipients = get_weekly_digest_recipients(
            role=role, days_ahead=days_ahead, reference_date=target_date
        )

        # Apply optional filters
        if email_filter:
            recipients = [
                r for r in recipients if r["email"].lower() == email_filter.lower()
            ]
        if user_id_filter:
            recipients = [
                r for r in recipients if r["user"] and r["user"].pk == user_id_filter
            ]

        total_eligible = len(recipients)
        to_send = []
        skipped_empty = 0

        for r in recipients:
            if not force and not r["data"]["has_content"]:
                skipped_empty += 1
                continue
            to_send.append(r)

        self.stdout.write(
            f"Found {total_eligible} eligible recipient(s). {len(to_send)} have content to send ({skipped_empty} skipped empty)."
        )

        if not to_send:
            self.stdout.write(self.style.SUCCESS("No emails to send."))
            return

        total_sent = 0
        for r in to_send:
            recip_role = r["role"]
            recip_email = r["email"]
            data = r["data"]

            ref_date = data.get("reference_date") or target_date or timezone.localdate()
            date_str = _format_ordinal_date(ref_date)

            if recip_role == "student":
                subject = f"Your Girls of Steel Portal Update for {date_str}"
                template_name = "programs/emails/weekly_digest_student.html"
            elif recip_role == "parent":
                subject = f"Your Girls of Steel Family Update for {date_str}"
                template_name = "programs/emails/weekly_digest_parent.html"
            else:  # mentor
                subject = f"Your Girls of Steel Mentor Update for {date_str}"
                template_name = "programs/emails/weekly_digest_mentor.html"

            if dry_run:
                self.stdout.write(
                    f"  [DRY RUN] Would send {recip_role} digest to {recip_email} (subject: '{subject}')"
                )
            else:
                try:
                    send_templated_notification(
                        subject=subject,
                        template_name=template_name,
                        context=data,
                        recipient_list=[recip_email],
                    )
                    total_sent += 1
                except Exception:
                    logger.exception(
                        "Failed to send weekly digest email to %s", recip_email
                    )

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"Dry run complete. {len(to_send)} email(s) would be sent."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(f"Successfully sent {total_sent} digest email(s).")
            )
