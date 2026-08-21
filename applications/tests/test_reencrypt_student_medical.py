import base64
from io import StringIO

from cryptography.fernet import Fernet
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, override_settings

from applications.models import Application
from programs.models import Program, School, Student

CURRENT_KEY = "ZmDfcTF7_60GrrY167zsiPd67pEvs0aGOv2oasOM1Pg="
TEST_SECRET = "test-secret-key-for-legacy-encryption!!"  # nosec B105


def _legacy_fernet(secret_key=TEST_SECRET):
    """Build a Fernet instance using the old SECRET_KEY-derived algorithm."""
    key = base64.urlsafe_b64encode(secret_key[:32].encode().ljust(32, b"\0"))
    return Fernet(key)


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
    student.refresh_from_db()


@override_settings(FILE_ENCRYPTION_KEY=CURRENT_KEY, SECRET_KEY=TEST_SECRET)
class ReencryptStudentMedicalTest(TestCase):
    """Tests for the ``reencrypt_student_medical`` management command."""

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

    # -- basic behaviour ---------------------------------------------------

    def test_reencrypts_legacy_key_fields(self):
        """Fields encrypted with the old SECRET_KEY-derived key should be
        recovered and re-encrypted with the current FILE_ENCRYPTION_KEY."""
        legacy = _legacy_fernet()
        _write_encrypted(
            self.student,
            {
                "allergies": "Peanuts",
                "dietary_restrictions": "Vegetarian",
                "medical_notes": "Asthma inhaler needed",
            },
            legacy,
        )

        out = StringIO()
        call_command("reencrypt_student_medical", stdout=out)

        self.student.refresh_from_db()
        self.assertEqual(self.student.allergies, "Peanuts")
        self.assertEqual(self.student.dietary_restrictions, "Vegetarian")
        self.assertEqual(self.student.medical_notes, "Asthma inhaler needed")
        self.assertIn("legacy key", out.getvalue())

    def test_reencrypts_unknown_key_fields_from_app_data(self):
        """Fields encrypted with an完全unknown key should fall back to
        Application source data."""
        unknown_key = Fernet.generate_key()
        _write_encrypted(
            self.student,
            {
                "allergies": "Peanuts",
                "dietary_restrictions": "Vegetarian",
                "medical_notes": "Asthma inhaler needed",
            },
            Fernet(unknown_key),
        )

        out = StringIO()
        call_command("reencrypt_student_medical", stdout=out)

        self.student.refresh_from_db()
        self.assertEqual(self.student.allergies, "Peanuts")
        self.assertEqual(self.student.dietary_restrictions, "Vegetarian")
        self.assertEqual(self.student.medical_notes, "Asthma inhaler needed")
        self.assertIn("app data", out.getvalue())

    def test_already_correct_fields_are_skipped(self):
        """Fields correctly encrypted with the current key should be skipped."""
        self.student.allergies = "Peanuts"
        self.student.dietary_restrictions = "Vegetarian"
        self.student.medical_notes = "Asthma inhaler needed"
        self.student.save()
        self.student.refresh_from_db()

        out = StringIO()
        call_command("reencrypt_student_medical", stdout=out)

        self.student.refresh_from_db()
        self.assertEqual(self.student.allergies, "Peanuts")
        self.assertEqual(self.student.dietary_restrictions, "Vegetarian")
        self.assertEqual(self.student.medical_notes, "Asthma inhaler needed")
        self.assertIn("0", out.getvalue())

    def test_partial_fields_updated(self):
        """Only non-empty Application fields should be written."""
        self.app.data = {
            "step5-student": {
                "allergies": "Shellfish",
                "dietary_restrictions": "",
                "medical_notes": "",
            },
        }
        self.app.save()
        unknown_key = Fernet.generate_key()
        _write_encrypted(
            self.student,
            {
                "allergies": "Peanuts",
                "dietary_restrictions": "Vegetarian",
                "medical_notes": "Asthma inhaler needed",
            },
            Fernet(unknown_key),
        )

        out = StringIO()
        call_command("reencrypt_student_medical", stdout=out)

        self.student.refresh_from_db()
        self.assertEqual(self.student.allergies, "Shellfish")

    # -- skip conditions ---------------------------------------------------

    def test_skips_unconverted_applications(self):
        """Students from unconverted Applications should be skipped."""
        self.app.status = Application.Status.SUBMITTED
        self.app.converted_student = None
        self.app.save()
        unknown_key = Fernet.generate_key()
        _write_encrypted(
            self.student,
            {"allergies": "Peanuts"},
            Fernet(unknown_key),
        )

        out = StringIO()
        call_command("reencrypt_student_medical", stdout=out)

        self.student.refresh_from_db()
        # Fields should still be garbled
        raw = self.student.__dict__.get("allergies")
        self.assertTrue(raw.startswith("gAAAAAB"))

    def test_skips_applications_without_student(self):
        """Applications without a converted_student FK should be skipped."""
        self.app.converted_student = None
        self.app.save()

        out = StringIO()
        call_command("reencrypt_student_medical", stdout=out)

    def test_skips_applications_without_medical_data(self):
        """Applications with empty/missing step5 medical fields should be skipped."""
        self.app.data = {"step5-student": {}}
        self.app.save()
        unknown_key = Fernet.generate_key()
        _write_encrypted(
            self.student,
            {"allergies": "Peanuts"},
            Fernet(unknown_key),
        )

        out = StringIO()
        call_command("reencrypt_student_medical", stdout=out)
        self.assertIn("0", out.getvalue())

    def test_no_converted_applications(self):
        """Command should succeed when there are no converted applications."""
        Application.objects.all().delete()
        out = StringIO()
        call_command("reencrypt_student_medical", stdout=out)
        self.assertIn("0", out.getvalue())

    # -- dry-run -----------------------------------------------------------

    def test_dry_run_does_not_write(self):
        """--dry-run should report what would change without persisting."""
        legacy = _legacy_fernet()
        _write_encrypted(
            self.student,
            {"allergies": "Peanuts"},
            legacy,
        )

        out = StringIO()
        call_command("reencrypt_student_medical", "--dry-run", stdout=out)

        self.student.refresh_from_db()
        # Fields should still be encrypted with legacy key (not re-encrypted)
        raw = self.student.__dict__.get("allergies")
        self.assertTrue(raw.startswith("gAAAAAB"))
        # Legacy key can still decrypt it (not re-encrypted with current key)
        self.assertIsNotNone(legacy.decrypt(raw.encode()))
        self.assertIn("DRY-RUN", out.getvalue())

    # -- --all-students flag ------------------------------------------------

    def test_all_students_processes_unlinked_students(self):
        """--all-students should process students without converted Applications."""
        orphan = Student.objects.create(
            legal_first_name="Grace",
            last_name="Hopper",
            date_of_birth="2009-06-01",
            school=School.objects.create(name="Other High"),
            graduation_year=2027,
        )
        legacy = _legacy_fernet()
        _write_encrypted(
            orphan,
            {
                "allergies": "Latex",
                "dietary_restrictions": "Gluten-free",
                "medical_notes": "",
            },
            legacy,
        )

        out = StringIO()
        call_command("reencrypt_student_medical", "--all-students", stdout=out)

        orphan.refresh_from_db()
        self.assertEqual(orphan.allergies, "Latex")
        self.assertEqual(orphan.dietary_restrictions, "Gluten-free")
        self.assertIn("legacy key", out.getvalue())

    def test_all_students_without_app_data_uses_legacy_key(self):
        """Students without Applications should still be recovered via the
        legacy SECRET_KEY-derived key."""
        orphan = Student.objects.create(
            legal_first_name="Alan",
            last_name="Turing",
            date_of_birth="2011-06-23",
            school=School.objects.create(name="CS High"),
            graduation_year=2029,
        )
        legacy = _legacy_fernet()
        _write_encrypted(
            orphan,
            {"allergies": "Pollen"},
            legacy,
        )

        out = StringIO()
        call_command("reencrypt_student_medical", "--all-students", stdout=out)

        orphan.refresh_from_db()
        self.assertEqual(orphan.allergies, "Pollen")
        self.assertIn("legacy key", out.getvalue())

    def test_all_students_skips_already_correct(self):
        """--all-students should still skip correctly encrypted fields."""
        self.student.allergies = "Peanuts"
        self.student.save()
        self.student.refresh_from_db()

        out = StringIO()
        call_command("reencrypt_student_medical", "--all-students", stdout=out)

        self.assertIn("0", out.getvalue())

    # -- output summary ----------------------------------------------------

    def test_summary_line_printed(self):
        """The command should print a summary of how many students were processed."""
        out = StringIO()
        call_command("reencrypt_student_medical", stdout=out)
        output_text = out.getvalue()
        self.assertIn("Done", output_text)
