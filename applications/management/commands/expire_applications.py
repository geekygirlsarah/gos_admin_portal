"""Expire old unfinished applications for one organization."""

from django.core.management.base import BaseCommand, CommandError

from applications.expiration import MAX_BATCH_SIZE, run_application_expiration_batch
from organizations.models import Organization


class Command(BaseCommand):
    help = "Preview or expire unfinished applications inactive for 30 days."

    def add_arguments(self, parser):
        parser.add_argument(
            "--organization",
            required=True,
            help="Slug of the active organization whose applications are checked.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=200,
            help=f"Maximum applications to process (1-{MAX_BATCH_SIZE}; default 200).",
        )
        parser.add_argument(
            "--execute",
            action="store_true",
            help="Delete expired applications and their owned uploaded documents.",
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

        summary = run_application_expiration_batch(
            organization,
            execute=options["execute"],
            limit=limit,
        )
        mode = "Expiration run" if options["execute"] else "Dry run"
        self.stdout.write(
            self.style.WARNING(
                f"{mode}: processed={summary['processed']}, "
                f"eligible={summary['eligible']}, deleted={summary['deleted']}, "
                f"skipped={summary['skipped']}, failed={summary['failed']}."
            )
        )
