from cryptography.fernet import InvalidToken
from django.core.management.base import BaseCommand

from applications.models import Application
from programs.models import _get_legacy_fernet, get_fernet

MEDICAL_FIELDS = ("allergies", "dietary_restrictions", "medical_notes")


class Command(BaseCommand):
    help = (
        "Re-encrypt Student medical/allergy fields. Fixes garbled ciphertext "
        "caused by an encryption key rotation (e.g. when FILE_ENCRYPTION_KEY "
        "was introduced after data was encrypted with SECRET_KEY). "
        "Strategy: current key -> legacy SECRET_KEY key -> Application data."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without saving.",
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _try_decrypt(ciphertext, fernet_instance):
        """Try to decrypt *ciphertext*; return the plaintext or None."""
        try:
            return fernet_instance.decrypt(ciphertext.encode()).decode()
        except (InvalidToken, UnicodeEncodeError, UnicodeDecodeError):
            return None

    def _recover_field(self, student, field_name, legacy_fernet, app):
        """Attempt to recover a garbled field.

        Returns ``(plaintext, source)`` or ``(None, None)`` if the field
        is already correct or unrecoverable.
        """
        raw = student.__dict__.get(field_name)
        if not raw or not isinstance(raw, str):
            return None, None

        # Already correctly encrypted with the current key.
        if self._try_decrypt(raw, get_fernet()) is not None:
            return None, None

        # Try the legacy SECRET_KEY-derived key.
        plaintext = self._try_decrypt(raw, legacy_fernet)
        if plaintext is not None:
            return plaintext, "legacy"

        # Last resort: Application source data.
        if app:
            step5 = (app.data or {}).get("step5-student") or {}
            app_value = (step5.get(field_name) or "").strip()
            if app_value:
                return app_value, "app"

        return None, None

    def _apply_updates(self, student, fields_to_write, dry_run):
        """Write recovered plaintext to *student* (or log in dry-run)."""
        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"[DRY-RUN] Would update {student} "
                    f"({', '.join(fields_to_write.keys())})"
                )
            )
        else:
            for field_name, value in fields_to_write.items():
                setattr(student, field_name, value)
            student.save()
            self.stdout.write(
                self.style.SUCCESS(
                    f"Updated {student} ({', '.join(fields_to_write.keys())})"
                )
            )

    # ------------------------------------------------------------------
    # Main handler
    # ------------------------------------------------------------------

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        app_lookup = self._build_app_lookup()

        from programs.models import Student

        students = Student.objects.all()

        legacy_fernet = _get_legacy_fernet()
        updated = 0
        skipped = 0
        recovered_legacy = 0
        recovered_app = 0

        for student in students:
            fields_to_write = {}
            for field in MEDICAL_FIELDS:
                app = app_lookup.get(student.id)
                plaintext, source = self._recover_field(
                    student, field, legacy_fernet, app
                )
                if plaintext is None:
                    continue
                fields_to_write[field] = plaintext
                if source == "legacy":
                    recovered_legacy += 1
                else:
                    recovered_app += 1

            if not fields_to_write:
                skipped += 1
                continue

            self._apply_updates(student, fields_to_write, dry_run)
            updated += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. Updated: {updated}, "
                f"Skipped (already OK): {skipped}, "
                f"Recovered (legacy key): {recovered_legacy}, "
                f"Recovered (app data): {recovered_app}"
            )
        )

    @staticmethod
    def _build_app_lookup():
        """Return ``{student_id: Application}`` for converted apps."""
        lookup = {}
        for app in Application.objects.filter(
            status=Application.Status.CONVERTED,
            converted_student__isnull=False,
        ).select_related("converted_student"):
            if app.converted_student_id:
                lookup[app.converted_student_id] = app
        return lookup
