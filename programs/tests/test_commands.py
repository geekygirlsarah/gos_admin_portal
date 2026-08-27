from datetime import date
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import models
from django.test import TestCase

from programs.models import (
    Adult,
    Enrollment,
    Fee,
    Payment,
    Program,
    School,
    SlidingScale,
    Student,
)


class SeedDbCommandTest(TestCase):
    def test_seed_db_command(self):
        """The seed command should create a large, varied modern dataset."""
        call_command("seed_db")

        today = date.today()

        # Check high-volume core records
        self.assertGreaterEqual(Program.objects.count(), 12)
        self.assertGreaterEqual(School.objects.count(), 4)
        self.assertGreaterEqual(Adult.objects.count(), 18)
        self.assertGreaterEqual(Student.objects.count(), 12)
        self.assertGreaterEqual(Enrollment.objects.count(), 30)

        # Check program date variety: past, current, and future offerings
        self.assertGreaterEqual(Program.objects.filter(end_date__lt=today).count(), 4)
        self.assertGreaterEqual(
            Program.objects.filter(start_date__lte=today, end_date__gte=today).count(),
            4,
        )
        self.assertGreaterEqual(Program.objects.filter(start_date__gt=today).count(), 4)

        # Check role variety among adults
        self.assertGreaterEqual(Adult.objects.filter(is_parent=True).count(), 12)
        self.assertGreaterEqual(Adult.objects.filter(is_mentor=True).count(), 4)
        self.assertGreaterEqual(Adult.objects.filter(is_alumni=True).count(), 2)

        # Check financial model coverage
        self.assertGreaterEqual(Fee.objects.count(), 20)
        self.assertGreaterEqual(Payment.objects.count(), 20)
        self.assertGreaterEqual(SlidingScale.objects.count(), 6)

        # Ensure payment states include paid-off, partially paid, and unpaid
        balance_profiles = {"paid": 0, "partial": 0, "unpaid": 0}
        for enrollment in Enrollment.objects.select_related("student", "program"):
            total_fees = Fee.objects.filter(program=enrollment.program).aggregate(
                total=models.Sum("amount")
            )["total"] or Decimal("0")
            total_payments = Payment.objects.filter(
                student=enrollment.student,
                program=enrollment.program,
            ).aggregate(total=models.Sum("amount"))["total"] or Decimal("0")

            if total_fees == Decimal("0"):
                continue
            if total_payments == Decimal("0"):
                balance_profiles["unpaid"] += 1
            elif total_payments < total_fees:
                balance_profiles["partial"] += 1
            elif total_payments >= total_fees:
                balance_profiles["paid"] += 1

        self.assertGreater(balance_profiles["paid"], 0)
        self.assertGreater(balance_profiles["partial"], 0)
        self.assertGreater(balance_profiles["unpaid"], 0)

    def test_seed_db_command_is_idempotent(self):
        """Running the command twice should not duplicate seeded entities."""
        call_command("seed_db")
        first_counts = {
            "programs": Program.objects.count(),
            "schools": School.objects.count(),
            "adults": Adult.objects.count(),
            "students": Student.objects.count(),
            "enrollments": Enrollment.objects.count(),
            "fees": Fee.objects.count(),
            "payments": Payment.objects.count(),
            "sliding_scales": SlidingScale.objects.count(),
        }

        call_command("seed_db")
        second_counts = {
            "programs": Program.objects.count(),
            "schools": School.objects.count(),
            "adults": Adult.objects.count(),
            "students": Student.objects.count(),
            "enrollments": Enrollment.objects.count(),
            "fees": Fee.objects.count(),
            "payments": Payment.objects.count(),
            "sliding_scales": SlidingScale.objects.count(),
        }

        self.assertEqual(first_counts, second_counts)


class GetProgramEmailsCommandTest(TestCase):
    def setUp(self):
        self.program = Program.objects.create(name="Summer Robotics")
        self.out = StringIO()

        # Students
        self.student_with_personal = Student.objects.create(
            legal_first_name="Alice",
            last_name="Student",
            personal_email="alice@example.com",
        )
        self.student_andrew_only = Student.objects.create(
            legal_first_name="Bob",
            last_name="Student",
            andrew_email="bob@andrew.cmu.edu",
        )
        self.student_no_email = Student.objects.create(
            legal_first_name="Charlie",
            last_name="Student",
        )
        self.graduate = Student.objects.create(
            legal_first_name="Dana",
            last_name="Grad",
            personal_email="dana@example.com",
            graduated=True,
        )

        # Enroll active students (not the graduate)
        Enrollment.objects.create(
            student=self.student_with_personal, program=self.program
        )
        Enrollment.objects.create(
            student=self.student_andrew_only, program=self.program
        )
        Enrollment.objects.create(student=self.student_no_email, program=self.program)

        # Parents
        self.parent_active = Adult.objects.create(
            preferred_first_name="Eve",
            last_name="Parent",
            personal_email="eve@example.com",
            is_parent=True,
            email_updates=True,
            login_enabled=True,
        )
        self.parent_no_updates = Adult.objects.create(
            legal_first_name="Frank",
            last_name="Parent",
            personal_email="frank@example.com",
            is_parent=True,
            email_updates=False,
            login_enabled=True,
        )
        self.parent_inactive = Adult.objects.create(
            legal_first_name="Grace",
            last_name="Parent",
            personal_email="grace@example.com",
            is_parent=True,
            email_updates=True,
            login_enabled=False,
        )

        # Link parents to active students
        self.parent_active.students.add(self.student_with_personal)
        self.parent_no_updates.students.add(self.student_with_personal)
        self.parent_inactive.students.add(self.student_with_personal)

        # Mentors
        self.mentor = Adult.objects.create(
            preferred_first_name="Hank",
            last_name="Mentor",
            andrew_email="hank@andrew.cmu.edu",
            is_mentor=True,
            login_enabled=True,
        )
        self.mentor_inactive = Adult.objects.create(
            legal_first_name="Iris",
            last_name="Mentor",
            personal_email="iris@example.com",
            is_mentor=True,
            mentor_active=False,
            login_enabled=False,
        )

    def test_students_flag(self):
        call_command(
            "get_program_emails",
            "--program-id",
            self.program.pk,
            "--students",
            stdout=self.out,
        )
        output = self.out.getvalue().strip()
        emails = [e.strip() for e in output.split(",")]
        self.assertIn("alice@example.com", emails)
        self.assertIn("bob@andrew.cmu.edu", emails)
        self.assertNotIn("dana@example.com", emails)  # graduated

    def test_parents_flag(self):
        call_command(
            "get_program_emails",
            "--program-id",
            self.program.pk,
            "--parents",
            stdout=self.out,
        )
        output = self.out.getvalue().strip()
        emails = [e.strip() for e in output.split(",")]
        self.assertIn("eve@example.com", emails)
        self.assertNotIn("frank@example.com", emails)  # email_updates=False
        self.assertNotIn("grace@example.com", emails)  # login_enabled=False

    def test_mentors_flag(self):
        call_command(
            "get_program_emails",
            "--program-id",
            self.program.pk,
            "--mentors",
            stdout=self.out,
        )
        output = self.out.getvalue().strip()
        emails = [e.strip() for e in output.split(",")]
        self.assertIn("hank@andrew.cmu.edu", emails)
        self.assertNotIn("iris@example.com", emails)  # inactive

    def test_multiple_groups(self):
        call_command(
            "get_program_emails",
            "--program-id",
            self.program.pk,
            "--students",
            "--parents",
            stdout=self.out,
        )
        output = self.out.getvalue().strip()
        emails = [e.strip() for e in output.split(",")]
        self.assertIn("alice@example.com", emails)
        self.assertIn("eve@example.com", emails)
        self.assertNotIn("hank@andrew.cmu.edu", emails)

    def test_no_recipients(self):
        # Empty program, no flags — warns about no addresses
        program = Program.objects.create(name="Empty Program")
        call_command(
            "get_program_emails",
            "--program-id",
            program.pk,
            "--students",
            stdout=self.out,
        )
        output = self.out.getvalue().strip()
        self.assertIn("No email addresses found", output)

    def test_deduplicates_emails(self):
        # Student and parent share the same email
        self.student_with_personal.personal_email = "shared@example.com"
        self.student_with_personal.save()
        self.parent_active.personal_email = "shared@example.com"
        self.parent_active.save()

        call_command(
            "get_program_emails",
            "--program-id",
            self.program.pk,
            "--students",
            "--parents",
            stdout=self.out,
        )
        output = self.out.getvalue().strip()
        emails = [e.strip() for e in output.split(",")]
        self.assertEqual(emails.count("shared@example.com"), 1)

    def test_output_is_comma_separated(self):
        call_command(
            "get_program_emails",
            "--program-id",
            self.program.pk,
            "--students",
            stdout=self.out,
        )
        output = self.out.getvalue().strip()
        self.assertIn(",", output)  # multiple students → comma-separated

    def test_program_id_required(self):
        with self.assertRaises(CommandError):
            call_command("get_program_emails", stdout=self.out)

    def test_no_group_flag_warns(self):
        call_command(
            "get_program_emails",
            "--program-id",
            self.program.pk,
            stdout=self.out,
        )
        output = self.out.getvalue().strip()
        self.assertIn("No recipient groups specified", output)
