"""Remove organization email history rows after the 30-day retention window."""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from programs.models import EmailHistory, EmailHistoryBatch

RETENTION_PERIOD = timedelta(days=30)


class Command(BaseCommand):
    help = "Delete outgoing email history at least 30 days old."

    def add_arguments(self, parser):
        parser.add_argument(
            "--execute",
            action="store_true",
            help="Delete eligible email history; without this option, only report a count.",
        )

    def handle(self, *args, **options):
        cutoff = timezone.now() - RETENTION_PERIOD
        eligible = EmailHistory.objects.filter(created_at__lt=cutoff)
        eligible_count = eligible.count()
        eligible_batch_ids = list(
            EmailHistoryBatch.objects.filter(created_at__lt=cutoff)
            .exclude(messages__created_at__gte=cutoff)
            .values_list("pk", flat=True)
        )

        if not options["execute"]:
            self.stdout.write(
                self.style.WARNING(
                    f"Dry run: {eligible_count} email history record(s) are eligible "
                    f"and {len(eligible_batch_ids)} batch record(s) are eligible "
                    "for deletion. Pass --execute to delete them."
                )
            )
            return

        eligible.delete()
        deleted_batch_count, _ = EmailHistoryBatch.objects.filter(
            pk__in=eligible_batch_ids
        ).delete()
        self.stdout.write(
            self.style.SUCCESS(
                f"Deleted {eligible_count} email history record(s) and "
                f"{deleted_batch_count} batch record(s)."
            )
        )
