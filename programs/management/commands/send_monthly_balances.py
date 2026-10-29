"""Management command to send monthly balance update emails to parents/guardians.

Usage:
    python manage.py send_monthly_balances
    python manage.py send_monthly_balances --all-balances
    python manage.py send_monthly_balances --days-back 30
    python manage.py send_monthly_balances --date 2026-11-01
    python manage.py send_monthly_balances --student-id 123
    python manage.py send_monthly_balances --dry-run
"""

import logging

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.dateparse import parse_date

from programs.services.balances_digest import get_monthly_balance_recipients
from programs.utils.notifications import send_templated_notification

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Send monthly balance statements to parents/guardians for students with program fees or dues owed."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would be sent without actually sending emails",
        )
        parser.add_argument(
            "--all-balances",
            action="store_true",
            help=(
                "Include students with zero/paid-in-full balances and older paid-off programs "
                "(default: only students with positive balance > $0 or transactions within the past month)"
            ),
        )
        parser.add_argument(
            "--days-back",
            type=int,
            default=31,
            help="Number of days back to check for recent transactions (default: 30)",
        )
        parser.add_argument(
            "--date",
            type=str,
            default=None,
            help="Explicit as-of date (YYYY-MM-DD) for calculating recent transactions and balances",
        )
        parser.add_argument(
            "--student-id",
            type=int,
            default=None,
            help="Target a single student by Student ID",
        )
        parser.add_argument(
            "--email",
            type=str,
            default=None,
            help="Target a specific parent email",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        all_balances = options["all_balances"]
        days_back = options["days_back"]
        date_str = options["date"]
        student_id = options["student_id"]
        email_filter = options["email"]

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

        positive_only = not all_balances
        self.stdout.write(
            f"Collecting monthly balance statements (positive_only={positive_only}, "
            f"student_id={student_id}, days_back={days_back}, as_of_date={target_date})..."
        )

        recipients = get_monthly_balance_recipients(
            positive_only=positive_only,
            student_id=student_id,
            as_of_date=target_date,
            days_back=days_back,
        )

        if email_filter:
            recipients = [
                r for r in recipients if r["email"].lower() == email_filter.lower()
            ]

        self.stdout.write(f"Found {len(recipients)} balance statement(s) to dispatch.")

        if not recipients:
            self.stdout.write(self.style.SUCCESS("No balance statements to send."))
            return

        statement_date = target_date or timezone.localdate()
        month_year_str = statement_date.strftime("%B %Y")

        total_sent = 0
        for r in recipients:
            student = r["student"]
            adult = r["adult"]
            recip_email = r["email"]
            data = r["data"]

            subject = f"Monthly Balance Statement for {student.display_name} - {month_year_str}"
            template_name = "programs/emails/monthly_balance_update.html"

            context = {
                "student": student,
                "adult": adult,
                "statement_date": statement_date,
                "program_balances": data["program_balances"],
                "total_balance": data["total_balance"],
                "total_fees": data["total_fees"],
                "total_payments": data["total_payments"],
                "total_sliding": data["total_sliding"],
            }

            if dry_run:
                self.stdout.write(
                    f"  [DRY RUN] Would send balance statement for {student.display_name} "
                    f"(balance: ${data['total_balance']}) to {recip_email}"
                )
            else:
                try:
                    send_templated_notification(
                        subject=subject,
                        template_name=template_name,
                        context=context,
                        recipient_list=[recip_email],
                    )
                    total_sent += 1
                except Exception:
                    logger.exception(
                        "Failed to send monthly balance email for %s to %s",
                        student,
                        recip_email,
                    )

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"Dry run complete. {len(recipients)} balance statement(s) would be sent."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Successfully sent {total_sent} balance statement(s)."
                )
            )
