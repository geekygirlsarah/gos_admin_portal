from django.core.management.base import BaseCommand
from django.utils import timezone

from programs.models import Adult, Student


class Command(BaseCommand):
    help = (
        "Convert graduating seniors (graduation_year <= current year) to alumni "
        "by creating/updating Adults and marking students as graduated."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--year",
            type=int,
            help="Graduation year cutoff (defaults to current year).",
        )
        parser.add_argument(
            "--include-inactive",
            action="store_true",
            help="Include students already inactive.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be changed without writing.",
        )
        # Back-compat: accept --no-deactivate but ignore it; also support --no-graduate
        parser.add_argument(
            "--no-deactivate",
            action="store_true",
            help="Deprecated (no longer deactivates). Kept for compatibility.",
        )
        parser.add_argument(
            "--no-graduate",
            action="store_true",
            help="Do not set Student.graduated=True during conversion.",
        )

    def handle(self, *args, **options):
        from programs.utils import convert_student_to_alumni

        year = options.get("year") or timezone.now().year
        include_inactive = options.get("include_inactive")
        dry_run = options.get("dry_run")
        no_graduate = options.get("no_graduate") or False

        qs = Student.objects.filter(graduation_year__lte=year)
        if not include_inactive:
            qs = qs.filter(graduated=False)

        created_count = 0
        existed_count = 0
        marked_graduated_count = 0
        total = qs.count()

        for student in qs.iterator():
            if dry_run:
                created_count += 1  # approximation
                marked_graduated_count += 1
                continue

            _adult, was_created, was_marked = convert_student_to_alumni(student)
            if was_created:
                created_count += 1
            else:
                existed_count += 1
            if was_marked:
                marked_graduated_count += 1

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN: No changes were written."))

        self.stdout.write(
            self.style.SUCCESS(
                f"Processed {total} students up to year {year}. "
                f"Adults created/updated as alumni: {created_count + existed_count} "
                f"(new: {created_count}, existing: {existed_count}), marked graduated: {marked_graduated_count}."
            )
        )
