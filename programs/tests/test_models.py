import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from programs.models import (
    Adult,
    Enrollment,
    Fee,
    FeeAssignment,
    Payment,
    Program,
    ProgramFeature,
    RaceEthnicity,
    RolePermission,
    School,
    Student,
)


class ModelTests(TestCase):
    def setUp(self):
        self.program = Program.objects.create(name="Robotics 2025")
        self.school = School.objects.create(name="Carnegie High")
        self.student = Student.objects.create(
            legal_first_name="Alex",
            last_name="Smith",
            school=self.school,
        )

    def test_program_year_display(self):
        # Case 1: No dates
        p = Program(name="Test")
        self.assertEqual(p.year_display, "")
        self.assertEqual(str(p), "Test")

        # Case 2: Same year
        p.start_date = datetime.date(2025, 1, 1)
        p.end_date = datetime.date(2025, 12, 31)
        self.assertEqual(p.year_display, "2025")
        self.assertEqual(str(p), "Test (2025)")

        # Case 3: Different years
        p.end_date = datetime.date(2026, 1, 1)
        self.assertEqual(p.year_display, "2025-2026")
        self.assertEqual(str(p), "Test (2025-2026)")

        # Case 4: Only start date
        p.end_date = None
        self.assertEqual(p.year_display, "2025")

        # Case 5: Only end date
        p.start_date = None
        p.end_date = datetime.date(2026, 1, 1)
        self.assertEqual(p.year_display, "2026")

    def test_program_has_feature(self):
        feat, _ = ProgramFeature.objects.get_or_create(
            key="discord", defaults={"name": "Discord"}
        )
        self.assertFalse(self.program.has_feature("discord"))
        self.program.features.add(feat)
        self.assertTrue(self.program.has_feature("discord"))
        self.assertFalse(self.program.has_feature("background-checks"))

    def test_payment_clean_requires_enrollment(self):
        pay = Payment(
            student=self.student,
            program=self.program,
            amount=Decimal("10.00"),
            paid_on=datetime.date.today(),
        )
        with self.assertRaises(ValidationError):
            pay.full_clean()
        # Enroll and then validate
        Enrollment.objects.create(student=self.student, program=self.program)
        pay.full_clean()  # no exception

    def test_fee_assignment_clean_requires_enrollment(self):
        fee = Fee.objects.create(
            program=self.program, name="Registration", amount=Decimal("50.00")
        )
        fa = FeeAssignment(fee=fee, student=self.student)
        # Not enrolled yet
        with self.assertRaises(ValidationError):
            fa.full_clean()
        # After enrollment it's valid
        Enrollment.objects.create(student=self.student, program=self.program)
        fa.full_clean()  # no exception

    def test_race_ethnicity_match_from_text(self):
        keys = [
            ("black-or-african-american", "Black or African-American"),
            ("hispanic-or-latino", "Hispanic or Latino"),
            ("asian", "Asian"),
            ("white", "White"),
            ("other", "Other"),
        ]
        for k, n in keys:
            RaceEthnicity.objects.get_or_create(key=k, defaults={"name": n})
        qs = RaceEthnicity.match_from_text("Black, Hispanic and Asian")
        self.assertSetEqual(
            set(qs.values_list("key", flat=True)),
            {"black-or-african-american", "hispanic-or-latino", "asian"},
        )
        qs2 = RaceEthnicity.match_from_text("Something totally unknown")
        self.assertIn("other", set(qs2.values_list("key", flat=True)))

    def test_student_save_does_not_override_parent_opt_out(self):
        # Saving a student must NOT silently flip a parent's explicit opt-out.
        # The email_updates preference is the parent's own choice and should
        # only be changed through the application wizard form validation.
        parent = Adult.objects.create(
            first_name="Pat", last_name="Smith", is_parent=True, email_updates=False
        )
        s = Student(legal_first_name="Riley", last_name="Jones", primary_contact=parent)
        s.save()
        parent.refresh_from_db()
        self.assertFalse(parent.email_updates)

    def test_adult_email_uniqueness(self):
        Adult.objects.create(first_name="A", last_name="B", email="test@example.com")
        # Second adult with same email should fail
        with self.assertRaises(Exception):
            Adult.objects.create(first_name="C", last_name="D", email="test@example.com")

    def test_adult_email_null_allowed_multiple_times(self):
        # Multiple adults with NULL email should be allowed
        Adult.objects.create(first_name="A", last_name="B", email=None)
        Adult.objects.create(first_name="C", last_name="D", email=None)
        # Succeeded if no exception

    def test_program_grade_range_fields(self):
        program = Program.objects.create(
            name="Test Program", grade_range_start=4, grade_range_end=6
        )
        self.assertEqual(program.grade_range_start, 4)
        self.assertEqual(program.grade_range_end, 6)

    def test_grade_range_display(self):
        p1 = Program.objects.create(name="P1", grade_range_start=4, grade_range_end=6)
        self.assertEqual(p1.grade_range_display, "4th–6th Grade")

        p2 = Program.objects.create(name="P2", grade_range_start=9, grade_range_end=12)
        self.assertEqual(p2.grade_range_display, "9th–12th Grade")

        p3 = Program.objects.create(name="P3", grade_range_start=0, grade_range_end=2)
        self.assertEqual(p3.grade_range_display, "K–2nd Grade")

        p4 = Program.objects.create(name="P4", grade_range_start=1, grade_range_end=1)
        self.assertEqual(p4.grade_range_display, "1st Grade")

        p5 = Program.objects.create(name="P5")
        self.assertEqual(p5.grade_range_display, "")


class RolePermissionTests(TestCase):
    def test_unique_role_section(self):
        RolePermission.objects.create(
            role="Mentor", section="student_info", can_read=True
        )
        with self.assertRaises(Exception):  # unique_together
            RolePermission.objects.create(
                role="Mentor", section="student_info", can_read=False
            )

    def test_str_representation(self):
        rp = RolePermission(
            role="Parent", section="payments", can_read=True, can_write=True
        )
        self.assertIn("Parent", str(rp))
        self.assertIn("Payments - General", str(rp))
        self.assertIn("R:True, W:True", str(rp))


# StudentApplicationTests removed; new application flow lives in the
# `applications` app and is tested there.


class UnsavedStudentTest(TestCase):
    def test_all_parents_on_unsaved_student(self):
        student = Student(legal_first_name="Unsaved", last_name="Student")
        # Accessing all_parents should not raise ValueError
        try:
            parents = student.all_parents
            self.assertEqual(parents, [])
        except ValueError as e:
            self.fail(f"all_parents() raised ValueError on unsaved student: {e}")
