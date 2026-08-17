"""Seed the initial Mentor Data Access Agreement from a markdown file.

Usage:
    python manage.py seed_mentor_agreement
    python manage.py seed_mentor_agreement --file path/to/policy.md
    python manage.py seed_mentor_agreement --agreement-version 2
    python manage.py seed_mentor_agreement --slug custom-slug
"""

from datetime import date
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from programs.models import MentorAgreement

DEFAULT_FILE = Path(__file__).resolve().parent.parent.parent.parent / "data_policy.md"
DEFAULT_SLUG = "data-access-policy"


class Command(BaseCommand):
    help = "Create or update a Mentor Agreement from a markdown file."

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            type=str,
            default=str(DEFAULT_FILE),
            help="Path to the markdown file (default: project root /gos_mentor_data_policy.md)",
        )
        parser.add_argument(
            "--slug",
            type=str,
            default=DEFAULT_SLUG,
            help="Slug identifier for the agreement (default: data-access-policy)",
        )
        parser.add_argument(
            "--agreement-version",
            type=int,
            default=None,
            help="Version number to use (default: next integer)",
        )
        parser.add_argument(
            "--deactivate",
            action="store_true",
            help="Deactivate all active versions of this slug",
        )

    def handle(self, *args, **options):
        slug = options["slug"]

        if options["deactivate"]:
            deactivated = MentorAgreement.objects.filter(
                is_active=True, slug=slug
            ).update(is_active=False)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Deactivated {deactivated} active agreement(s) for '{slug}'."
                )
            )
            return

        file_path = Path(options["file"])
        if not file_path.exists():
            raise CommandError(f"File not found: {file_path}")

        content = file_path.read_text(encoding="utf-8")

        version = options["agreement_version"]
        if version is None:
            last = (
                MentorAgreement.objects.filter(slug=slug).order_by("-version").first()
            )
            version = (last.version + 1) if last else 1

        title = "Girls of Steel Mentor Data Access and Safeguarding Agreement"

        agreement, created = MentorAgreement.objects.update_or_create(
            slug=slug,
            version=version,
            defaults={
                "title": title,
                "content": content,
                "effective_date": date.today(),
                "is_active": True,
            },
        )

        if created:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Created '{slug}' agreement version {version} (active)."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Updated '{slug}' agreement version {version} (active)."
                )
            )
