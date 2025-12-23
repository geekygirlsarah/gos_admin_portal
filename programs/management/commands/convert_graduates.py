from django.core.management.base import BaseCommand
from django.utils import timezone

from programs.models import Adult, Student


class Command(BaseCommand):
    help = "Convert graduating seniors (graduation_year <= current year) to alumni by creating/updating Adults and marking students as graduated."

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
        year = options.get("year") or timezone.now().year
        include_inactive = options.get("include_inactive")
        dry_run = options.get("dry_run")
        no_graduate = options.get("no_graduate") or False

        qs = Student.objects.filter(graduation_year__lte=year)
        if not include_inactive:
            qs = qs.filter(active=True)

        created = 0
        existed = 0
        marked_graduated = 0
        total = qs.count()

        def find_matching_adult(s: Student):
            emails = [s.personal_email, s.andrew_email]
            for e in emails:
                if e:
                    a = Adult.objects.filter(alumni_email__iexact=e).first()
                    if a:
                        return a
                    a = Adult.objects.filter(email__iexact=e, is_alumni=True).first()
                    if a:
                        return a
            first = (s.first_name or s.legal_first_name or "").strip()
            last = (s.last_name or "").strip()
            if first and last:
                return Adult.objects.filter(
                    first_name__iexact=first, last_name__iexact=last, is_alumni=True
                ).first()
            return None

        for student in qs.iterator():
            adult = find_matching_adult(student)
            if not adult:
                if not dry_run:
                    Adult.objects.create(
                        first_name=student.first_name or student.legal_first_name or "",
                        last_name=student.last_name or "",
                        alumni_email=student.personal_email or student.andrew_email,
                        is_alumni=True,
                    )
                created += 1
            else:
                existed += 1
                if not dry_run:
                    changed = False
                    if not adult.is_alumni:
                        adult.is_alumni = True
                        changed = True
                    if not adult.alumni_email and (
                        student.personal_email or student.andrew_email
                    ):
                        adult.alumni_email = (
                            student.personal_email or student.andrew_email
                        )
                        changed = True
                    if changed:
                        adult.save(
                            update_fields=["is_alumni", "alumni_email", "updated_at"]
                        )
            if not no_graduate and not student.graduated:
                marked_graduated += 1
                if not dry_run:
                    student.graduated = True
                    student.save(update_fields=["graduated", "updated_at"])

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN: No changes were written."))

        self.stdout.write(
            self.style.SUCCESS(
                f"Processed {total} students up to year {year}. Adults created/updated as alumni: {created + existed} (new: {created}, existing: {existed}), marked graduated: {marked_graduated}."
            )
        )
