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
from programs.utils.balance_email import (
    get_balance_email_from_address,
    get_balance_email_settings,
)
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
                "Include zero/paid-in-full programs and older paid-off programs "
                "(default: only programs with a positive balance or recent transactions)"
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
            f"days_back={days_back}, as_of_date={target_date})..."
        )
        logger.info(
            "Collecting monthly balance statements (positive_only=%s, days_back=%s, as_of_date=%s, dry_run=%s)",
            positive_only,
            days_back,
            target_date,
            dry_run,
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
        logger.info("Found %d balance statement(s) to dispatch.", len(recipients))

        if not recipients:
            self.stdout.write(self.style.SUCCESS("No balance statements to send."))
            logger.info("No balance statements to send.")
            return

        statement_date = target_date or timezone.localdate()
        month_year_str = statement_date.strftime("%B %Y")

        total_sent = 0
        branding_by_org = {}
        for r in recipients:
            student = r["student"]
            program = r["program"]
            adult = r["adult"]
            recip_email = r["email"]
            data = r["data"]
            organization = student.organization

            subject = (
                f"Monthly Balance Statement for {student.display_name} - "
                f"{program.name} - {month_year_str}"
            )
            template_name = "programs/emails/monthly_balance_update.html"

            context = {
                "student": student,
                "program": program,
                "adult": adult,
                "statement_date": statement_date,
                "program_balances": data["program_balances"],
                "total_balance": data["total_balance"],
                "total_fees": data["total_fees"],
                "total_payments": data["total_payments"],
                "total_sliding": data["total_sliding"],
            }

            if dry_run:
                self.stdout.write("  [DRY RUN] Would send a balance statement.")
                logger.info("[DRY RUN] Would send a monthly balance statement.")
            else:
                try:
                    if organization.pk not in branding_by_org:
                        branding_by_org[organization.pk] = get_balance_email_settings(
                            organization
                        )
                    org_settings = branding_by_org[organization.pk]
                    context["org_email_settings"] = org_settings
                    send_templated_notification(
                        subject=subject,
                        template_name=template_name,
                        context=context,
                        recipient_list=[recip_email],
                        from_email=get_balance_email_from_address(org_settings),
                        organization=organization,
                    )
                    total_sent += 1
                    logger.info("Sent monthly balance statement.")
                except Exception as exc:
                    logger.error(
                        "Failed to send monthly balance statement (error=%s).",
                        type(exc).__name__,
                    )

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"Dry run complete. {len(recipients)} balance statement(s) would be sent."
                )
            )
            logger.info(
                "Dry run complete for monthly balances. %d balance statement(s) would be sent.",
                len(recipients),
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Successfully sent {total_sent} balance statement(s)."
                )
            )
            logger.info(
                "Completed monthly balances: successfully sent %d balance statement(s)",
                total_sent,
            )
