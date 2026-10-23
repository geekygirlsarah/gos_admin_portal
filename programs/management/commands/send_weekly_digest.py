"""Management command to send automated weekly digest emails to students, parents, and mentors.

Usage:
    python manage.py send_weekly_digest
    python manage.py send_weekly_digest --days-ahead 7
    python manage.py send_weekly_digest --role student
    python manage.py send_weekly_digest --dry-run
"""

import logging

from django.core.management.base import BaseCommand

from programs.services.digest import get_weekly_digest_recipients
from programs.utils.notifications import send_templated_notification

logger = logging.getLogger(__name__)


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
        dry_run = options["dry_run"]
        role = options["role"]
        email_filter = options["email"]
        user_id_filter = options["user_id"]
        force = options["force"]

        self.stdout.write(
            f"Collecting weekly digest data (role={role}, days_ahead={days_ahead})..."
        )

        recipients = get_weekly_digest_recipients(role=role, days_ahead=days_ahead)

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

            if recip_role == "student":
                subject = f"Weekly Update for {r['recipient_obj'].display_name}"
                template_name = "programs/emails/weekly_digest_student.html"
            elif recip_role == "parent":
                subject = "Weekly Family Update - Girls of Steel"
                template_name = "programs/emails/weekly_digest_parent.html"
            else:  # mentor
                subject = "Mentor Weekly Operations Update - Girls of Steel"
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
