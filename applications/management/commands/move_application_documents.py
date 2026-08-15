import os

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand

from applications.models import Application
from programs.models import StudentDocument


class Command(BaseCommand):
    help = "Move signed documents from converted applications to student profiles."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Do not actually copy files.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        apps = Application.objects.filter(
            converted_student__isnull=False, status=Application.Status.CONVERTED
        ).prefetch_related("document_submissions")

        count = 0
        skipped = 0
        errors = 0

        for app in apps:
            student = app.converted_student
            for submission in app.document_submissions.all():
                if not submission.file:
                    continue

                # Check if it already exists
                if StudentDocument.objects.filter(
                    student=student, program_document=submission.document
                ).exists():
                    skipped += 1
                    continue

                if dry_run:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"[DRY-RUN] Would copy {submission.file.name} to {student}"
                        )
                    )
                    count += 1
                    continue

                try:
                    filename = os.path.basename(submission.file.name)
                    with submission.file.open("rb") as f:
                        content = f.read()
                        student_doc = StudentDocument(
                            student=student, program_document=submission.document
                        )
                        student_doc.file.save(filename, ContentFile(content), save=True)

                    self.stdout.write(
                        self.style.SUCCESS(
                            f"Copied {submission.file.name} to {student}"
                        )
                    )
                    count += 1
                except (FileNotFoundError, IOError) as e:
                    self.stdout.write(
                        self.style.WARNING(
                            f"Failed to copy {submission.file.name}: {e}"
                        )
                    )
                    errors += 1
                except Exception as e:
                    self.stdout.write(
                        self.style.ERROR(
                            f"Unexpected error copying {submission.file.name}: {e}"
                        )
                    )
                    errors += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. Copied: {count}, Skipped (already exist): {skipped}, Errors: {errors}"
            )
        )
