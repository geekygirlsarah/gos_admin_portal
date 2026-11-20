from django.core.management.base import BaseCommand, CommandError

from organizations.models import Organization
from travel.cleanup import cleanup_signed_file
from travel.models import TravelSignedFileDeletion


class Command(BaseCommand):
    help = "Retry pending signed travel file cleanup for one organization."

    def add_arguments(self, parser):
        parser.add_argument("--organization-id", type=int, required=True)

    def handle(self, *args, **options):
        organization_id = options["organization_id"]
        if not Organization.objects.filter(pk=organization_id).exists():
            raise CommandError("Organization not found.")
        pending_ids = list(
            TravelSignedFileDeletion.objects.for_organization(organization_id)
            .filter(organization_id=organization_id)
            .order_by("pk")
            .values_list("pk", flat=True)
        )
        completed = sum(
            cleanup_signed_file(deletion_id, organization_id)
            for deletion_id in pending_ids
        )
        self.stdout.write(
            f"Signed travel file cleanup: {completed} completed, "
            f"{len(pending_ids) - completed} pending."
        )
