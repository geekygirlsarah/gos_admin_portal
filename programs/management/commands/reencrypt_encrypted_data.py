"""Re-encrypt all encrypted model fields after a cryptographic key rotation.

Covers every ``EncryptedTextField`` / ``EncryptedCharField`` and
``EncryptedFileField`` in the project (currently Student medical fields and
``TaxForm.file``).

Key inputs:
  * Old decryptors (any of these may match existing ciphertext):
      - ``--old-file-key`` / ``OLD_FILE_ENCRYPTION_KEY``: a rotated-away
        ``FILE_ENCRYPTION_KEY``.
      - ``--old-secret-key`` / ``OLD_SECRET_KEY``: a rotated-away
        ``SECRET_KEY`` (used to derive the legacy file-encryption key).
      - the currently-configured ``FILE_ENCRYPTION_KEY``.
      - the currently-configured ``SECRET_KEY`` (legacy derivation, kept for
        backwards compatibility with the pre-``FILE_ENCRYPTION_KEY`` era).
  * Target encryptor (defaults to the current ``FILE_ENCRYPTION_KEY``):
      - ``--new-file-key`` / ``NEW_FILE_ENCRYPTION_KEY``: pre-encrypt data with
        a key before it becomes the configured value.

Student medical fields that no key can open fall back to the source data in
their converted Application.
"""

import base64
import os
from collections import Counter

from cryptography.fernet import Fernet, InvalidToken
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from programs.models import (
    EncryptedCharField,
    EncryptedFileField,
    EncryptedTextField,
    _get_legacy_fernet,
    get_fernet,
)

MEDICAL_FIELDS = ("allergies", "dietary_restrictions", "medical_notes")


def _try_decrypt(ciphertext, fernet):
    """Decrypt *ciphertext* (str or bytes) or return None."""
    try:
        if isinstance(ciphertext, bytes):
            return fernet.decrypt(ciphertext)
        return fernet.decrypt(ciphertext.encode()).decode()
    except (
        InvalidToken,
        ValueError,
        UnicodeEncodeError,
        UnicodeDecodeError,
    ):
        return None


def _legacy_fernet_from_secret(secret_key):
    """Derive the legacy file-encryption Fernet key from *secret_key*."""
    key = base64.urlsafe_b64encode(secret_key[:32].encode().ljust(32, b"\0"))
    return Fernet(key)


class Command(BaseCommand):
    help = (
        "Re-encrypt every encrypted model field with the current "
        "FILE_ENCRYPTION_KEY (see module docstring). "
        "Run with --dry-run first to preview what would change."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--old-file-key",
            dest="old_file_key",
            default=None,
            help="A rotated-away FILE_ENCRYPTION_KEY that can decrypt existing "
            "data. May also be set via the OLD_FILE_ENCRYPTION_KEY env var.",
        )
        parser.add_argument(
            "--old-secret-key",
            dest="old_secret_key",
            default=None,
            help="A rotated-away SECRET_KEY used to derive the legacy file "
            "encryption key. May also be set via the OLD_SECRET_KEY env var.",
        )
        parser.add_argument(
            "--new-file-key",
            dest="new_file_key",
            default=None,
            help="Fernet key to re-encrypt data with, overriding the currently "
            "configured FILE_ENCRYPTION_KEY (useful to pre-encrypt before "
            "deploying a key rotation). May also be set via the "
            "NEW_FILE_ENCRYPTION_KEY env var.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without writing anything.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        new_key_value = options["new_file_key"] or os.environ.get(
            "NEW_FILE_ENCRYPTION_KEY"
        )
        old_file_key_value = options["old_file_key"] or os.environ.get(
            "OLD_FILE_ENCRYPTION_KEY"
        )
        old_secret_key_value = options["old_secret_key"] or os.environ.get(
            "OLD_SECRET_KEY"
        )

        try:
            current_fernet = get_fernet()
        except RuntimeError as exc:
            current_fernet = None
            self.stdout.write(self.style.WARNING(str(exc)))

        if new_key_value:
            target_fernet = self._fernet_from_value(
                new_key_value,
                "--new-file-key / NEW_FILE_ENCRYPTION_KEY",
            )
        elif current_fernet is not None:
            target_fernet = current_fernet
        else:
            raise CommandError(
                "No key to re-encrypt with. Set FILE_ENCRYPTION_KEY or pass "
                "--new-file-key / NEW_FILE_ENCRYPTION_KEY."
            )

        # Ordered candidates. The first one that decrypts a value wins.
        decryptors = [("current key", target_fernet)]
        if current_fernet is not None:
            decryptors.append(("current settings key", current_fernet))
        if old_file_key_value:
            decryptors.append(
                (
                    "old file key (CLI/env)",
                    self._fernet_from_value(
                        old_file_key_value,
                        "--old-file-key / OLD_FILE_ENCRYPTION_KEY",
                    ),
                )
            )
        if old_secret_key_value:
            decryptors.append(
                (
                    "legacy SECRET_KEY key (CLI/env)",
                    _legacy_fernet_from_secret(old_secret_key_value),
                )
            )
        decryptors.append(
            (
                "legacy SECRET_KEY key (current settings)",
                _get_legacy_fernet(),
            )
        )

        self.updated = 0
        self.skipped = 0
        self.failed = 0
        self.sources = Counter()

        app_lookup = self._build_app_lookup()

        for model, fields in self._iter_encrypted_models():
            text_fields = [f for kind, f in fields if kind == "text"]
            file_fields = [f for kind, f in fields if kind == "file"]
            if text_fields:
                self._reencrypt_text_fields(
                    model,
                    text_fields,
                    target_fernet,
                    decryptors,
                    app_lookup,
                    dry_run,
                )
            if file_fields:
                self._reencrypt_file_fields(
                    model, file_fields, target_fernet, decryptors, dry_run
                )

        self._print_summary()

    # ------------------------------------------------------------------
    # Key parsing / model discovery
    # ------------------------------------------------------------------

    @staticmethod
    def _fernet_from_value(value, label):
        try:
            return Fernet(value.encode() if isinstance(value, str) else value)
        except (ValueError, TypeError) as exc:
            raise CommandError(f"Invalid Fernet key for {label}: {exc}")

    @staticmethod
    def _iter_encrypted_models():
        """Yield ``(model, [(kind, field), ...])`` for every model that has
        encrypted fields, so future encrypted columns are covered automatically."""
        from django.apps import apps

        for model in apps.get_models():
            fields = []
            for field in model._meta.get_fields(include_parents=True):
                if isinstance(field, (EncryptedTextField, EncryptedCharField)):
                    fields.append(("text", field))
                elif isinstance(field, EncryptedFileField):
                    fields.append(("file", field))
            if fields:
                yield model, fields

    @staticmethod
    def _build_app_lookup():
        """Return ``{student_id: step5-student dict}`` from converted apps."""
        from applications.models import Application

        lookup = {}
        for _app_id, student_id, data in Application.objects.filter(
            status=Application.Status.CONVERTED,
            converted_student__isnull=False,
        ).values_list("pk", "converted_student_id", "data"):
            step5 = (data or {}).get("step5-student") or {}
            lookup[student_id] = step5
        return lookup

    # ------------------------------------------------------------------
    # Text fields (EncryptedTextField / EncryptedCharField)
    # ------------------------------------------------------------------

    def _reencrypt_text_fields(
        self, model, fields, target_fernet, decryptors, app_lookup, dry_run
    ):
        pk_col = model._meta.pk.column
        columns = ", ".join(f'"{f.column}"' for f in fields)
        # Identifiers come from model metadata (never user input).
        sql = (
            f'SELECT "{pk_col}", {columns} FROM "{model._meta.db_table}"'  # nosec B608
        )
        with connection.cursor() as cursor:
            cursor.execute(sql)
            rows = cursor.fetchall()
        self.stdout.write(
            self.style.NOTICE(
                f"Scanning {len(rows)} {model._meta.verbose_name_plural} row(s)..."
            )
        )

        for row in rows:
            pk, *values = row
            for field, raw in zip(fields, values):
                if raw is None or raw == "":
                    continue
                if _try_decrypt(raw, target_fernet) is not None:
                    self.skipped += 1
                    continue

                plaintext = None
                source = None
                for label, fernet in decryptors:
                    plaintext = _try_decrypt(raw, fernet)
                    if plaintext is not None:
                        source = label
                        break

                if (
                    plaintext is None
                    and model.__name__ == "Student"
                    and field.name in MEDICAL_FIELDS
                ):
                    step5 = app_lookup.get(pk) or {}
                    app_value = (step5.get(field.name) or "").strip()
                    if app_value:
                        plaintext, source = app_value, "application data"

                if plaintext is None:
                    self.failed += 1
                    self.stdout.write(
                        self.style.ERROR(
                            f"Could not decrypt {model.__name__}#{pk} "
                            f"field {field.name}"
                        )
                    )
                    continue

                if dry_run:
                    self.stdout.write(
                        self.style.WARNING(
                            f"[DRY-RUN] Would re-encrypt {model.__name__}#{pk} "
                            f"field {field.name}"
                        )
                    )
                    continue

                self._write_text_field(
                    model, pk_col, pk, field, plaintext, target_fernet
                )
                self.sources[source] += 1
                self.updated += 1

    @staticmethod
    def _write_text_field(model, pk_col, pk, field, plaintext, target_fernet):
        cipher = target_fernet.encrypt(plaintext.encode()).decode()
        sql = (
            # B608: identifiers come from model metadata, never user input.
            f'UPDATE "{model._meta.db_table}" SET "{field.column}" = %s '  # nosec B608
            f'WHERE "{pk_col}" = %s'
        )
        with connection.cursor() as cursor:
            cursor.execute(sql, [cipher, pk])

    # ------------------------------------------------------------------
    # File fields (EncryptedFileField)
    # ------------------------------------------------------------------

    def _reencrypt_file_fields(self, model, fields, target_fernet, decryptors, dry_run):
        for instance in model.objects.all().iterator():
            for field in fields:
                name = getattr(getattr(instance, field.name), "name", None) or ""
                if not name:
                    continue
                try:
                    with default_storage.open(name, "rb") as fh:
                        raw = fh.read()
                except Exception as exc:  # noqa: BLE001 - report-before-continue
                    self.failed += 1
                    self.stdout.write(
                        self.style.ERROR(f"Could not read file '{name}': {exc}")
                    )
                    continue

                if _try_decrypt(raw, target_fernet) is not None:
                    self.skipped += 1
                    continue

                plaintext = None
                source = None
                for label, fernet in decryptors:
                    plaintext = _try_decrypt(raw, fernet)
                    if plaintext is not None:
                        source = label
                        break

                if plaintext is None:
                    self.failed += 1
                    self.stdout.write(
                        self.style.ERROR(f"Could not decrypt file '{name}'")
                    )
                    continue

                if dry_run:
                    self.stdout.write(
                        self.style.WARNING(f"[DRY-RUN] Would re-encrypt file '{name}'")
                    )
                    continue

                try:
                    self._rewrite_file(name, raw, plaintext, target_fernet)
                except Exception as exc:  # noqa: BLE001
                    self.failed += 1
                    self.stdout.write(
                        self.style.ERROR(f"Failed to re-encrypt file '{name}': {exc}")
                    )
                    continue
                self.sources[source] += 1
                self.updated += 1

    @staticmethod
    def _rewrite_file(name, original_raw, plaintext, target_fernet):
        encrypted = target_fernet.encrypt(plaintext)
        try:
            with default_storage.open(name, "wb") as fh:
                fh.write(encrypted)
        except Exception:  # noqa: BLE001 - keep scanning other files
            # Restore the original ciphertext so a failed rewrite is not data loss.
            try:
                with default_storage.open(name, "wb") as fh:
                    fh.write(original_raw)
            except Exception:  # noqa: BLE001 - restore is best-effort
                pass  # nosec B110
            raise

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def _print_summary(self):
        summary = (
            f"Done. Updated: {self.updated}, "
            f"Skipped (already current): {self.skipped}, "
            f"Failed (unrecoverable): {self.failed}"
        )
        if self.sources:
            parts = ", ".join(
                f"{label}: {count}" for label, count in self.sources.most_common()
            )
            summary += f". Recovered via: {parts}"
        self.stdout.write(self.style.SUCCESS(summary))
