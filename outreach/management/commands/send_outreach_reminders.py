"""Management command to send automated reminder emails for upcoming outreach events.

Usage:
    python manage.py send_outreach_reminders
    python manage.py send_outreach_reminders --days-ahead 1
    python manage.py send_outreach_reminders --dry-run
    python manage.py send_outreach_reminders --date 2026-11-15
"""

import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.dateparse import parse_date

from outreach.models import OutreachShift
from outreach.services import (
    adult_recipient_email,
    notify_mentor_outreach_reminder,
    notify_outreach_reminder,
    student_recipient_email,
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Send reminder emails to students and mentors signed up for outreach events "
        "happening the next day."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--days-ahead",
            type=int,
            default=1,
            help="Number of days ahead to look for outreach events (default: 1)",
        )
        parser.add_argument(
            "--date",
            type=str,
            default=None,
            help="Explicit target date (YYYY-MM-DD) for outreach events",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would be sent without actually sending emails",
        )
        parser.add_argument(
            "--email",
            type=str,
            default=None,
            help="Filter to send only to a specific recipient email",
        )

    def handle(self, *args, **options):
        days_ahead = options["days_ahead"]
        dry_run = options["dry_run"]
        email_filter = options["email"]
        date_str = options["date"]

        if date_str:
            target_date = parse_date(date_str)
            if not target_date:
                self.stderr.write(
                    self.style.ERROR(
                        f"Invalid date format: {date_str}. Use YYYY-MM-DD."
                    )
                )
                return
        else:
            target_date = timezone.localdate() + timedelta(days=days_ahead)

        self.stdout.write(f"Checking for outreach events on {target_date}...")

        shifts = (
            OutreachShift.objects.filter(date=target_date)
            .select_related("event", "event__program")
            .prefetch_related(
                "signups__student__user",
                "mentor_signups__adult__user",
            )
            .order_by("start_time")
        )

        if not shifts.exists():
            self.stdout.write(
                self.style.SUCCESS(f"No outreach shifts found for {target_date}.")
            )
            return

        total_sent = 0
        email_filter_lower = email_filter.lower().strip() if email_filter else None

        for shift in shifts:
            event = shift.event
            self.stdout.write(
                f"Processing shift for event '{event.name}' "
                f"({shift.date} {shift.start_time}-{shift.end_time})..."
            )

            # Student signups
            for signup in shift.signups.all():
                email = student_recipient_email(signup.student)
                if not email:
                    continue
                if email_filter_lower and email.lower() != email_filter_lower:
                    continue

                if dry_run:
                    self.stdout.write(
                        f"  [DRY RUN] Would send reminder to student "
                        f"{signup.student.display_name} <{email}> for '{event.name}' "
                        f"({signup.get_role_display()})"
                    )
                else:
                    if notify_outreach_reminder(signup):
                        total_sent += 1
                        self.stdout.write(
                            f"  Sent reminder to {email} for '{event.name}'"
                        )

            # Mentor signups
            for mentor_signup in shift.mentor_signups.all():
                email = adult_recipient_email(mentor_signup.adult)
                if not email:
                    continue
                if email_filter_lower and email.lower() != email_filter_lower:
                    continue

                if dry_run:
                    self.stdout.write(
                        f"  [DRY RUN] Would send reminder to mentor "
                        f"{mentor_signup.adult.display_name} <{email}> for '{event.name}'"
                    )
                else:
                    if notify_mentor_outreach_reminder(mentor_signup):
                        total_sent += 1
                        self.stdout.write(
                            f"  Sent reminder to mentor {email} for '{event.name}'"
                        )

        if dry_run:
            self.stdout.write(
                self.style.WARNING("Dry run complete. No emails were sent.")
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Successfully sent {total_sent} outreach reminder email(s)."
                )
            )
