"""Send organization-scoped application inactivity and deadline reminders."""

from django.core.management.base import BaseCommand, CommandError

from applications.reminders import MAX_BATCH_SIZE, run_application_reminder_batch
from organizations.models import Organization


class Command(BaseCommand):
    help = "Send due application reminders for one active organization."

    def add_arguments(self, parser):
        parser.add_argument(
            "--organization",
            required=True,
            help="Slug of the active organization whose reminders should be checked.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=200,
            help=f"Maximum applications to process (1-{MAX_BATCH_SIZE}; default 200).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show the count of eligible reminders without claiming or sending.",
        )

    def handle(self, *args, **options):
        slug = options["organization"].strip()
        organization = Organization.objects.filter(
            slug__iexact=slug,
            is_active=True,
        ).first()
        if organization is None:
            raise CommandError("An active organization with that slug was not found.")
        limit = options["limit"]
        if not 1 <= limit <= MAX_BATCH_SIZE:
            raise CommandError(f"--limit must be between 1 and {MAX_BATCH_SIZE}.")

        summary = run_application_reminder_batch(
            organization,
            dry_run=options["dry_run"],
            limit=limit,
        )
        if options["dry_run"]:
            self.stdout.write(
                self.style.WARNING(
                    f"Dry run: {summary['planned']} reminder message(s) would be sent. "
                    "No email was sent and no delivery claim was created."
                )
            )
            return

        self.stdout.write(
            self.style.SUCCESS(
                "Application reminders complete: "
                f"sent={summary['sent']}, failed={summary['failed']}, "
                f"unknown={summary['unknown']}, skipped={summary['skipped']}."
            )
        )
