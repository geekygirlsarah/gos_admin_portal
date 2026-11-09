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
from django.db.models import F, Prefetch, Q
from django.utils import timezone
from django.utils.dateparse import parse_date

from outreach.models import OutreachMentorSignup, OutreachShift, OutreachSignup
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
        logger.info(
            "Checking for outreach events on %s (days_ahead=%s, dry_run=%s, email_filter=%s)",
            target_date,
            days_ahead,
            dry_run,
            email_filter,
        )

        local_signups = (
            OutreachSignup.objects.filter(
                organization_id=F("shift__organization_id"),
                student__organization_id=F("shift__organization_id"),
                shift__event__organization_id=F("shift__organization_id"),
            )
            .filter(
                Q(shift__event__program__isnull=True)
                | Q(shift__event__program__organization_id=F("shift__organization_id"))
            )
            .select_related("student__user")
        )
        local_mentor_signups = (
            OutreachMentorSignup.objects.filter(
                organization_id=F("shift__organization_id"),
                adult__organization_id=F("shift__organization_id"),
                shift__event__organization_id=F("shift__organization_id"),
            )
            .filter(
                Q(shift__event__program__isnull=True)
                | Q(shift__event__program__organization_id=F("shift__organization_id"))
            )
            .select_related("adult__user")
        )
        shifts = (
            OutreachShift.objects.filter(
                date=target_date,
                event__organization_id=F("organization_id"),
            )
            .filter(
                Q(event__program__isnull=True)
                | Q(event__program__organization_id=F("organization_id"))
            )
            .select_related("event", "event__program")
            .prefetch_related(
                Prefetch("signups", queryset=local_signups),
                Prefetch("mentor_signups", queryset=local_mentor_signups),
            )
            .order_by("start_time")
        )

        if not shifts.exists():
            self.stdout.write(
                self.style.SUCCESS(f"No outreach shifts found for {target_date}.")
            )
            logger.info("No outreach shifts found for %s", target_date)
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

                student_user = getattr(signup.student, "user", None)
                username = student_user.username if student_user else "N/A"

                if dry_run:
                    self.stdout.write(
                        f"  [DRY RUN] Would send reminder to student "
                        f"{signup.student.display_name} <{email}> for '{event.name}' "
                        f"({signup.get_role_display()})"
                    )
                    logger.info(
                        "[DRY RUN] Would send outreach reminder to student %s (user: %s) <%s> for event '%s' (%s)",
                        signup.student.display_name,
                        username,
                        email,
                        event.name,
                        signup.get_role_display(),
                    )
                else:
                    if notify_outreach_reminder(signup):
                        total_sent += 1
                        self.stdout.write(
                            f"  Sent reminder to {email} for '{event.name}'"
                        )
                        logger.info(
                            "Sent outreach reminder to student %s (user: %s) <%s> for event '%s' (%s)",
                            signup.student.display_name,
                            username,
                            email,
                            event.name,
                            signup.get_role_display(),
                        )

            # Mentor signups
            for mentor_signup in shift.mentor_signups.all():
                email = adult_recipient_email(mentor_signup.adult)
                if not email:
                    continue
                if email_filter_lower and email.lower() != email_filter_lower:
                    continue

                mentor_user = getattr(mentor_signup.adult, "user", None)
                username = mentor_user.username if mentor_user else "N/A"

                if dry_run:
                    self.stdout.write(
                        f"  [DRY RUN] Would send reminder to mentor "
                        f"{mentor_signup.adult.display_name} <{email}> for '{event.name}'"
                    )
                    logger.info(
                        "[DRY RUN] Would send outreach reminder to mentor %s (user: %s) <%s> for event '%s'",
                        mentor_signup.adult.display_name,
                        username,
                        email,
                        event.name,
                    )
                else:
                    if notify_mentor_outreach_reminder(mentor_signup):
                        total_sent += 1
                        self.stdout.write(
                            f"  Sent reminder to mentor {email} for '{event.name}'"
                        )
                        logger.info(
                            "Sent outreach reminder to mentor %s (user: %s) <%s> for event '%s'",
                            mentor_signup.adult.display_name,
                            username,
                            email,
                            event.name,
                        )

        if dry_run:
            self.stdout.write(
                self.style.WARNING("Dry run complete. No emails were sent.")
            )
            logger.info("Dry run complete for outreach reminders. No emails were sent.")
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Successfully sent {total_sent} outreach reminder email(s)."
                )
            )
            logger.info(
                "Completed outreach reminders: successfully sent %d email(s)",
                total_sent,
            )
