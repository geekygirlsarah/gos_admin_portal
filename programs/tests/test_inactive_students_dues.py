import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils.crypto import get_random_string

from programs.models import Enrollment, Fee, FeeAssignment, Program, Student


class InactiveStudentsDuesTest(TestCase):
    def setUp(self):
        password = get_random_string(32)
        self.user = User.objects.create_superuser(
            username="admin", password=password, email="admin@example.com"
        )
        self.client.login(username="admin", password=password)
        self.program = Program.objects.create(name="Test Program")

    def test_inactive_students_separated(self):
        # Active Student
        student_active = Student.objects.create(
            legal_first_name="Active", last_name="Student"
        )
        Enrollment.objects.create(
            student=student_active, program=self.program, active=True
        )

        # Inactive Student
        student_inactive = Student.objects.create(
            legal_first_name="Inactive", last_name="Student"
        )
        Enrollment.objects.create(
            student=student_inactive, program=self.program, active=False
        )

        # Graduated Student (should also be inactive)
        student_graduated = Student.objects.create(
            legal_first_name="Graduated", last_name="Student", graduated=True
        )
        Enrollment.objects.create(
            student=student_graduated, program=self.program, active=True
        )

        url = reverse("program_dues_owed", args=[self.program.pk])
        response = self.client.get(url)

        # Check active rows
        active_rows = response.context["active_rows"]
        self.assertEqual(len(active_rows), 1)
        self.assertEqual(active_rows[0]["student"], student_active)

        # Check inactive rows
        inactive_rows = response.context["inactive_rows"]
        self.assertEqual(len(inactive_rows), 2)
        # Ordered by name: "Graduated Student" then "Inactive Student"
        self.assertEqual(inactive_rows[0]["student"], student_graduated)
        self.assertEqual(inactive_rows[1]["student"], student_inactive)

        # Verify grand total includes all
        self.assertEqual(len(active_rows) + len(inactive_rows), 3)
