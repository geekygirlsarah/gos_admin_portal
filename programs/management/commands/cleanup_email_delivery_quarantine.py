"""Remove encrypted email-delivery quarantine rows after the 30-day retention window."""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from programs.models import EmailDeliveryQuarantine

RETENTION_PERIOD = timedelta(days=30)


class Command(BaseCommand):
    help = "Delete quarantined email delivery events at least 30 days old."

    def add_arguments(self, parser):
        parser.add_argument(
            "--execute",
            action="store_true",
            help="Delete eligible quarantine rows; without this option, only report a count.",
        )

    def handle(self, *args, **options):
        cutoff = timezone.now() - RETENTION_PERIOD
        eligible = EmailDeliveryQuarantine.objects.filter(received_at__lte=cutoff)
        eligible_count = eligible.count()

        if not options["execute"]:
            self.stdout.write(
                self.style.WARNING(
                    f"Dry run: {eligible_count} quarantined email delivery event(s) "
                    "are eligible for deletion. Pass --execute to delete them."
                )
            )
            return

        deleted_count, _ = eligible.delete()
        self.stdout.write(
            self.style.SUCCESS(
                f"Deleted {deleted_count} quarantined email delivery event(s)."
            )
        )
