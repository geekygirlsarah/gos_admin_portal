import datetime

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from programs.models import (
    Adult,
    AdultStudentRelationship,
    Enrollment,
    Program,
    Student,
)


class DashboardStatusTests(TestCase):
    """
    Permanent regression tests for dashboard program status grouping and collapsing.
    """

    def setUp(self):
        # Setup Student
        self.student_user = User.objects.create_user(
            username="student", password="password123"  # nosec B106
        )
        self.student = Student.objects.create(
            user=self.student_user, first_name="Student", last_name="User"
        )

        # Setup Parent
        self.parent_user = User.objects.create_user(
            username="parent", password="password123"  # nosec B106
        )
        self.parent = Adult.objects.create(
            user=self.parent_user, first_name="Parent", last_name="User", is_parent=True
        )
        AdultStudentRelationship.objects.create(
            adult=self.parent, student=self.student, relationship_to_student="parent"
        )

        today = datetime.date.today()

        # Active Program
        self.active_program = Program.objects.create(
            name="Active Program",
            active=True,
            start_date=today - datetime.timedelta(days=10),
            end_date=today + datetime.timedelta(days=10),
        )
        Enrollment.objects.create(student=self.student, program=self.active_program)

        # Upcoming Program
        self.upcoming_program = Program.objects.create(
            name="Upcoming Program",
            active=True,
            start_date=today + datetime.timedelta(days=10),
            end_date=today + datetime.timedelta(days=20),
        )
        Enrollment.objects.create(student=self.student, program=self.upcoming_program)

        # Inactive Program
        self.inactive_program = Program.objects.create(
            name="Inactive Program",
            active=False,
            start_date=today - datetime.timedelta(days=20),
            end_date=today - datetime.timedelta(days=10),
        )
        Enrollment.objects.create(student=self.student, program=self.inactive_program)

        # Withdrawn Program (Active program, but student is inactive)
        self.withdrawn_program = Program.objects.create(
            name="Withdrawn Program",
            active=True,
            start_date=today - datetime.timedelta(days=5),
            end_date=today + datetime.timedelta(days=5),
        )
        Enrollment.objects.create(
            student=self.student, program=self.withdrawn_program, active=False
        )

    def test_student_dashboard_groups_programs(self):
        self.client.login(username="student", password="password123")  # nosec B106
        response = self.client.get(reverse("profile_dashboard"))
        self.assertEqual(response.status_code, 200)

        # Active program should be expanded (has "Dates:")
        self.assertContains(response, "Active Program")
        self.assertContains(response, "<strong>Dates:</strong>", count=1)

        # Others should be in accordion
        self.assertContains(response, "Upcoming Program")
        self.assertContains(response, "Inactive Program")
        self.assertContains(response, "Withdrawn Program")
        self.assertContains(response, "Withdrawn")
        self.assertContains(response, 'id="otherProgramsAccordion"')
        self.assertContains(response, "Past")
        self.assertContains(response, "Upcoming Programs")
        self.assertContains(response, "(3)")

    def test_parent_dashboard_groups_programs(self):
        self.client.login(username="parent", password="password123")  # nosec B106
        response = self.client.get(reverse("profile_dashboard"))
        self.assertEqual(response.status_code, 200)

        # Active program should be expanded (has "Balance Owed:")
        self.assertContains(response, "Active Program")
        self.assertContains(response, "Balance Owed:", count=1)

        # Others should be in student-specific accordion
        self.assertContains(response, "Upcoming Program")
        self.assertContains(response, "Inactive Program")
        self.assertContains(response, "Withdrawn Program")
        self.assertContains(response, "Withdrawn")
        self.assertContains(response, f'id="otherProgramsAccordion{self.student.pk}"')
        self.assertContains(response, "Past")
        self.assertContains(response, "Upcoming Programs")
        self.assertContains(response, "(3)")

    def test_mentor_dashboard_shows_active_programs(self):
        # Setup Mentor
        mentor_user = User.objects.create_user(
            username="mentor", password="password123"  # nosec B106
        )
        Adult.objects.create(
            user=mentor_user, first_name="Mentor", last_name="User", is_mentor=True
        )

        self.client.login(username="mentor", password="password123")  # nosec B106
        response = self.client.get(reverse("profile_dashboard"))
        self.assertEqual(response.status_code, 200)

        # Should show Active Program
        self.assertContains(response, "Active Program")
        # Should also show Withdrawn Program because the program itself is ACTIVE
        self.assertContains(response, "Withdrawn Program")

        # Should NOT show Upcoming or Inactive programs
        self.assertNotContains(response, "Upcoming Program")
        self.assertNotContains(response, "Inactive Program")

        # Should have a link to details
        self.assertContains(
            response, reverse("program_detail", args=[self.active_program.pk])
        )
