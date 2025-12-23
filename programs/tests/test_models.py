import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from programs.models import (
    Program,
    ProgramFeature,
    School,
    Student,
    Adult,
    Enrollment,
    Fee,
    Payment,
    FeeAssignment,
    RaceEthnicity,
    RolePermission,
    StudentApplication,
)


class ModelTests(TestCase):
    def setUp(self):
        self.program = Program.objects.create(name="Robotics 2025", year=2025)
        self.school = School.objects.create(name="Carnegie High")
        self.student = Student.objects.create(
            legal_first_name="Alex",
            last_name="Smith",
            school=self.school,
        )

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

    def test_payment_clean_fee_program_must_match(self):
        Enrollment.objects.create(student=self.student, program=self.program)
        other_program = Program.objects.create(name="Other Program")
        fee_other = Fee.objects.create(
            program=other_program, name="Dues", amount=Decimal("25.00")
        )
        pay = Payment(
            student=self.student,
            program=self.program,
            fee=fee_other,
            amount=Decimal("10.00"),
            paid_on=datetime.date.today(),
        )
        with self.assertRaises(ValidationError):
            pay.full_clean()

    def test_payment_clean_fee_assignments_enforced(self):
        Enrollment.objects.create(student=self.student, program=self.program)
        fee = Fee.objects.create(
            program=self.program, name="Kit", amount=Decimal("100.00")
        )
        # Assign fee to a different student
        other = Student.objects.create(legal_first_name="Jamie", last_name="Lee")
        FeeAssignment.objects.create(fee=fee, student=other)
        pay = Payment(
            student=self.student,
            program=self.program,
            fee=fee,
            amount=Decimal("20.00"),
            paid_on=datetime.date.today(),
        )
        with self.assertRaises(ValidationError):
            pay.full_clean()
        # Assign also to target student -> should pass
        FeeAssignment.objects.create(fee=fee, student=self.student)
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

    def test_student_save_auto_opt_in_primary_parent(self):
        parent = Adult.objects.create(
            first_name="Pat", last_name="Smith", is_parent=True, email_updates=False
        )
        s = Student(legal_first_name="Riley", last_name="Jones", primary_contact=parent)
        s.save()
        parent.refresh_from_db()
        self.assertTrue(parent.email_updates)


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


class StudentApplicationTests(TestCase):
    def setUp(self):
        self.program = Program.objects.create(name="App Program", year=2025)
        self.app = StudentApplication.objects.create(
            program=self.program,
            legal_first_name="App",
            last_name="Test",
            personal_email="app@example.com",
            date_of_birth=datetime.date(2010, 1, 1),
            parent_name="Parent App",
            parent_email="parent@example.com",
        )

    def test_approve_creates_records(self):
        student = self.app.approve()
        self.assertIsInstance(student, Student)
        self.assertEqual(student.legal_first_name, "App")
        self.assertEqual(student.personal_email, "app@example.com")
        self.assertTrue(
            Enrollment.objects.filter(student=student, program=self.program).exists()
        )
        self.app.refresh_from_db()
        self.assertEqual(self.app.status, "accepted")
