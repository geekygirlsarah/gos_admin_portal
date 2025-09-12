from django.core.management.base import BaseCommand
from django.utils import timezone

from programs.models import Student, Alumni


class Command(BaseCommand):
    help = "Convert graduating seniors (graduation_year <= current year) to alumni by creating Alumni records and optionally deactivating students."

    def add_arguments(self, parser):
        parser.add_argument('--year', type=int, help='Graduation year cutoff (defaults to current year).')
        parser.add_argument('--include-inactive', action='store_true', help='Include students already inactive.')
        parser.add_argument('--dry-run', action='store_true', help='Show what would be changed without writing.')
        parser.add_argument('--no-deactivate', action='store_true', help='Do not set Student.active=False during conversion.')

    def handle(self, *args, **options):
        year = options.get('year') or timezone.now().year
        include_inactive = options.get('include_inactive')
        dry_run = options.get('dry_run')
        no_deactivate = options.get('no_deactivate')

        qs = Student.objects.filter(graduation_year__lte=year)
        if not include_inactive:
            qs = qs.filter(active=True)

        created = 0
        existed = 0
        deactivated = 0
        total = qs.count()

        for student in qs.iterator():
            alumni, was_created = Alumni.objects.get_or_create(student=student)
            if was_created:
                created += 1
            else:
                existed += 1
            if not no_deactivate and student.active:
                deactivated += 1
                if not dry_run:
                    student.active = False
                    student.save(update_fields=['active', 'updated_at'])

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN: No changes were written.'))

        self.stdout.write(self.style.SUCCESS(
            f"Processed {total} students up to year {year}. Alumni created: {created}, existing: {existed}, deactivated: {deactivated}."
        ))
