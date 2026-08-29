"""Tests for the ``reencrypt_encrypted_data`` management command.

Covers recovering encrypted Student medical text fields and TaxForm
file uploads after a key rotation, wiring old keys in via CLI flags or
environment variables, and targeting a brand-new key with
``--new-file-key``.
"""

import base64
import os
import tempfile
from io import StringIO
from unittest import mock

from cryptography.fernet import Fernet
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, override_settings

from applications.models import Application
from programs.models import Program, School, SlidingScale, Student, TaxForm

CURRENT_KEY = "ZmDfcTF7_60GrrY167zsiPd67pEvs0aGOv2oasOM1Pg="
TEST_SECRET = "test-secret-key-for-legacy-encryption!!"  # nosec B105


def _legacy_fernet(secret_key=TEST_SECRET):
    """Build a Fernet instance using the SECRET_KEY-derived algorithm."""
    key = base64.urlsafe_b64encode(secret_key[:32].encode().ljust(32, b"\0"))
    return Fernet(key)


def _legacy_fernet_for(secret_key):
    return _legacy_fernet(secret_key)


def _write_encrypted(student, plaintext_fields, fernet_instance):
    """Write ciphertext produced by *fernet_instance* directly to the DB
    via raw SQL (bypassing the model layer entirely)."""
    set_clauses = []
    params = []
    for field_name, plaintext in plaintext_fields.items():
        set_clauses.append(f"{field_name} = %s")
        params.append(fernet_instance.encrypt(plaintext.encode()).decode())
    params.append(student.pk)

    sql = "UPDATE programs_student "
    sql += f"SET {', '.join(set_clauses)} WHERE id = %s"
    with connection.cursor() as cursor:
        cursor.execute(sql, params)


def _read_raw_text(student, field_name):
    with connection.cursor() as cursor:
        cursor.execute(
            f'SELECT "{field_name}" FROM programs_student WHERE id = %s',  # nosec B608
            [student.pk],
        )
        return cursor.fetchone()[0]


def _write_encrypted_file(tax_form, filename, plaintext, fernet_instance):
    """Store ciphertext produced by *fernet_instance* on the default storage
    and point the TaxForm at it."""
    name = default_storage.save(
        f"tax_forms/{filename}",
        ContentFile(fernet_instance.encrypt(plaintext)),
    )
    TaxForm.objects.filter(pk=tax_form.pk).update(file=name)
    return name


@override_settings(FILE_ENCRYPTION_KEY=CURRENT_KEY, SECRET_KEY=TEST_SECRET)
class ReencryptEncryptedDataTextTest(TestCase):
    """Text-field recovery (Student medical fields)."""

    def setUp(self):
        school = School.objects.create(name="Test High")
        self.program = Program.objects.create(
            name="Robotics",
            start_date="2026-01-01",
            end_date="2026-06-01",
            active=True,
        )
        self.student = Student.objects.create(
            legal_first_name="Ada",
            last_name="Lovelace",
            date_of_birth="2010-01-01",
            school=school,
            graduation_year=2028,
        )
        self.app = Application.objects.create(
            applicant_type=Application.Type.STUDENT,
            email="ada@example.com",
            program=self.program,
            status=Application.Status.CONVERTED,
            converted_student=self.student,
            data={
                "step5-student": {
                    "allergies": "Peanuts",
                    "dietary_restrictions": "Vegetarian",
                    "medical_notes": "Asthma inhaler needed",
                },
            },
        )
        self.medical = {
            "allergies": "Peanuts",
            "dietary_restrictions": "Vegetarian",
            "medical_notes": "Asthma inhaler needed",
        }

    def test_reencrypts_legacy_secret_key_fields(self):
        """Fields encrypted with the legacy SECRET_KEY-derived key should be
        recovered with the current SECRET_KEY fallback and re-encrypted."""
        _write_encrypted(self.student, self.medical, _legacy_fernet())

        out = StringIO()
        call_command("reencrypt_encrypted_data", stdout=out)

        self.student.refresh_from_db()
        self.assertEqual(self.student.allergies, "Peanuts")
        self.assertEqual(self.student.dietary_restrictions, "Vegetarian")
        self.assertEqual(self.student.medical_notes, "Asthma inhaler needed")
        self.assertIn("legacy", out.getvalue())

    def test_reencrypts_legacy_fields_without_app(self):
        """Students with no converted Application should still be recovered
        via the legacy SECRET_KEY-derived key."""
        orphan = Student.objects.create(
            legal_first_name="Grace",
            last_name="Hopper",
            date_of_birth="2009-06-01",
            school=School.objects.create(name="Other High"),
            graduation_year=2027,
        )
        _write_encrypted(orphan, {"allergies": "Latex"}, _legacy_fernet())

        out = StringIO()
        call_command("reencrypt_encrypted_data", stdout=out)

        orphan.refresh_from_db()
        self.assertEqual(orphan.allergies, "Latex")
        self.assertIn("legacy", out.getvalue())

    def test_reencrypts_old_file_key_via_cli(self):
        """A rotated-away FILE_ENCRYPTION_KEY can be supplied via CLI and its
        ciphertext recovered."""
        old_key = Fernet.generate_key()
        _write_encrypted(self.student, self.medical, Fernet(old_key))

        out = StringIO()
        call_command(
            "reencrypt_encrypted_data",
            old_file_key=old_key.decode(),
            stdout=out,
        )

        self.student.refresh_from_db()
        self.assertEqual(self.student.allergies, "Peanuts")
        self.assertEqual(self.student.dietary_restrictions, "Vegetarian")
        self.assertEqual(self.student.medical_notes, "Asthma inhaler needed")
        self.assertIn("old file key", out.getvalue())

    def test_reencrypts_old_file_key_via_env(self):
        """The old FILE_ENCRYPTION_KEY can also come from the
        OLD_FILE_ENCRYPTION_KEY environment variable."""
        old_key = Fernet.generate_key()
        _write_encrypted(self.student, self.medical, Fernet(old_key))

        out = StringIO()
        with mock.patch.dict(os.environ, {"OLD_FILE_ENCRYPTION_KEY": old_key.decode()}):
            call_command("reencrypt_encrypted_data", stdout=out)

        self.student.refresh_from_db()
        self.assertEqual(self.student.allergies, "Peanuts")
        self.assertEqual(self.student.dietary_restrictions, "Vegetarian")
        self.assertEqual(self.student.medical_notes, "Asthma inhaler needed")
        self.assertIn("old file key", out.getvalue())

    def test_reencrypts_old_secret_key_via_env(self):
        """A rotated-away SECRET_KEY can be supplied via OLD_SECRET_KEY and its
        derived ciphertext recovered."""
        old_secret = "the-old-rotated-secret-key-value!!"  # nosec B105
        _write_encrypted(
            self.student, {"allergies": "Peanuts"}, _legacy_fernet_for(old_secret)
        )

        out = StringIO()
        with mock.patch.dict(os.environ, {"OLD_SECRET_KEY": old_secret}):
            call_command("reencrypt_encrypted_data", stdout=out)

        self.student.refresh_from_db()
        self.assertEqual(self.student.allergies, "Peanuts")
        self.assertIn("legacy", out.getvalue())

    def test_new_file_key_cli_targets_different_key(self):
        """``--new-file-key`` re-encrypts with a key different from the current
        settings key (pre-deploy rotation flow)."""
        _write_encrypted(self.student, {"allergies": "Peanuts"}, _legacy_fernet())
        new_key = Fernet.generate_key()

        out = StringIO()
        call_command(
            "reencrypt_encrypted_data",
            new_file_key=new_key.decode(),
            stdout=out,
        )

        raw = _read_raw_text(self.student, "allergies")
        with self.assertRaises(Exception):
            Fernet(CURRENT_KEY.encode()).decrypt(raw.encode())
        self.assertEqual(Fernet(new_key).decrypt(raw.encode()).decode(), "Peanuts")

    def test_application_data_fallback(self):
        """Unknown ciphertext falls back to converted Application data."""
        unknown_key = Fernet.generate_key()
        _write_encrypted(self.student, self.medical, Fernet(unknown_key))

        out = StringIO()
        call_command("reencrypt_encrypted_data", stdout=out)

        self.student.refresh_from_db()
        self.assertEqual(self.student.allergies, "Peanuts")
        self.assertEqual(self.student.dietary_restrictions, "Vegetarian")
        self.assertEqual(self.student.medical_notes, "Asthma inhaler needed")
        self.assertIn("application data", out.getvalue())

    def test_already_correct_fields_are_skipped(self):
        """Fields correctly encrypted with the current key should be skipped."""
        self.student.allergies = "Peanuts"
        self.student.dietary_restrictions = "Vegetarian"
        self.student.medical_notes = "Asthma inhaler needed"
        self.student.save()
        self.student.refresh_from_db()

        out = StringIO()
        call_command("reencrypt_encrypted_data", stdout=out)

        self.student.refresh_from_db()
        self.assertEqual(self.student.allergies, "Peanuts")
        self.assertIn("Updated: 0", out.getvalue())

    def test_unrecoverable_fields_unchanged(self):
        """Ciphertext that no key can open is reported and left untouched."""
        unknown_key = Fernet.generate_key()
        _write_encrypted(
            self.student,
            {"allergies": "Peanuts"},
            Fernet(unknown_key),
        )
        self.app.delete()

        out = StringIO()
        call_command("reencrypt_encrypted_data", stdout=out)

        raw = _read_raw_text(self.student, "allergies")
        self.assertTrue(raw.startswith("gAAAAAB"))
        self.assertIn("unrecoverable", out.getvalue())

    def test_dry_run_does_not_write(self):
        """--dry-run reports what would change without persisting."""
        _write_encrypted(self.student, {"allergies": "Peanuts"}, _legacy_fernet())

        out = StringIO()
        call_command("reencrypt_encrypted_data", "--dry-run", stdout=out)

        raw = _read_raw_text(self.student, "allergies")
        self.assertIsNotNone(_legacy_fernet().decrypt(raw.encode()))
        self.assertIn("DRY-RUN", out.getvalue())


@override_settings(
    FILE_ENCRYPTION_KEY=CURRENT_KEY,
    SECRET_KEY=TEST_SECRET,
    MEDIA_ROOT=tempfile.mkdtemp(prefix="test-media-"),
)
class ReencryptEncryptedDataFileTest(TestCase):
    """EncryptedFileField recovery (TaxForm.file)."""

    def setUp(self):
        school = School.objects.create(name="Test High")
        self.student = Student.objects.create(
            legal_first_name="Ada",
            last_name="Lovelace",
            date_of_birth="2010-01-01",
            school=school,
            graduation_year=2028,
        )
        self.sliding_scale = SlidingScale.objects.create(student=self.student)

    def test_reencrypts_tax_form_file_with_old_file_key(self):
        """A TaxForm file encrypted with an old FILE_ENCRYPTION_KEY should be
        decrypted, re-encrypted with the current key, and readable again."""
        old_key = Fernet.generate_key()
        plaintext = b"TAX DOCUMENT 1040"
        tax_form = TaxForm.objects.create(sliding_scale=self.sliding_scale)
        name = _write_encrypted_file(tax_form, "doc.pdf", plaintext, Fernet(old_key))

        out = StringIO()
        call_command(
            "reencrypt_encrypted_data",
            old_file_key=old_key.decode(),
            stdout=out,
        )

        raw = default_storage.open(name, "rb").read()
        self.assertEqual(Fernet(CURRENT_KEY.encode()).decrypt(raw), plaintext)
        tax_form.refresh_from_db()
        with tax_form.file.open("rb") as f:
            self.assertEqual(f.read(), plaintext)
        self.assertIn("old file key", out.getvalue())

    def test_tax_form_file_encrypted_with_current_key_skipped(self):
        """Files already encrypted with the current key should be skipped."""
        tax_form = TaxForm.objects.create(
            sliding_scale=self.sliding_scale,
            file=SimpleUploadedFile("doc.pdf", b"current-key file"),
        )
        name = tax_form.file.name

        out = StringIO()
        call_command("reencrypt_encrypted_data", stdout=out)

        raw = default_storage.open(name, "rb").read()
        self.assertEqual(Fernet(CURRENT_KEY.encode()).decrypt(raw), b"current-key file")
        self.assertIn("Updated: 0", out.getvalue())

    def test_dry_run_does_not_rewrite_file(self):
        """--dry-run leaves file ciphertext untouched and reports what would
        change."""
        old_key = Fernet.generate_key()
        plaintext = b"DRY RUN FILE"
        tax_form = TaxForm.objects.create(sliding_scale=self.sliding_scale)
        name = _write_encrypted_file(tax_form, "doc.pdf", plaintext, Fernet(old_key))

        out = StringIO()
        call_command(
            "reencrypt_encrypted_data",
            old_file_key=old_key.decode(),
            dry_run=True,
            stdout=out,
        )

        raw = default_storage.open(name, "rb").read()
        self.assertEqual(Fernet(old_key).decrypt(raw), plaintext)
        self.assertIn("DRY-RUN", out.getvalue())

    def test_tax_form_file_targeted_with_new_file_key(self):
        """``--new-file-key`` re-encrypts file content with a key different from
        the current settings key."""
        old_key = Fernet.generate_key()
        plaintext = b"PRE-DEPLOY ROTATION"
        tax_form = TaxForm.objects.create(sliding_scale=self.sliding_scale)
        name = _write_encrypted_file(tax_form, "doc.pdf", plaintext, Fernet(old_key))
        new_key = Fernet.generate_key()

        out = StringIO()
        call_command(
            "reencrypt_encrypted_data",
            old_file_key=old_key.decode(),
            new_file_key=new_key.decode(),
            stdout=out,
        )

        raw = default_storage.open(name, "rb").read()
        self.assertEqual(Fernet(new_key).decrypt(raw), plaintext)
        with self.assertRaises(Exception):
            Fernet(CURRENT_KEY.encode()).decrypt(raw)

    def test_file_rewrite_failure_is_counted_as_failed(self):
        """A storage write failure should be reported as a failure, never as an
        update, and should leave the original ciphertext in place."""
        old_key = Fernet.generate_key()
        plaintext = b"REWRITE FAIL FILE"
        tax_form = TaxForm.objects.create(sliding_scale=self.sliding_scale)
        name = _write_encrypted_file(tax_form, "doc.pdf", plaintext, Fernet(old_key))

        original_open = default_storage.__class__.open

        def fail_on_write(self, open_name, mode="rb"):
            if mode == "wb":
                raise OSError("disk full")
            return original_open(self, open_name, mode)

        out = StringIO()
        with mock.patch.object(default_storage, "open", fail_on_write):
            call_command(
                "reencrypt_encrypted_data",
                old_file_key=old_key.decode(),
                stdout=out,
            )

        self.assertIn("Updated: 0", out.getvalue())
        self.assertIn("Failed (unrecoverable): 1", out.getvalue())
        raw = default_storage.open(name, "rb").read()
        self.assertEqual(Fernet(old_key).decrypt(raw), plaintext)
