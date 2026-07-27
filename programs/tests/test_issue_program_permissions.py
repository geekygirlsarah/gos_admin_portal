from datetime import timedelta

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from programs.models import Adult, Enrollment, Program, RolePermission, Student


class ProgramPermissionTests(TestCase):
    def setUp(self):
        # Create a Lead Mentor
        self.lead_mentor_user = User.objects.create_user(
            username="lead_mentor", password="password123"
        )  # nosec B106
        lm_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.lead_mentor_user.groups.add(lm_group)

        # Create a Mentor
        self.mentor_user = User.objects.create_user(
            username="mentor_user", password="password123"
        )  # nosec B106
        self.mentor_adult = Adult.objects.create(
            user=self.mentor_user, first_name="Mentor", last_name="User", is_mentor=True
        )

        # Create a Parent
        self.parent_user = User.objects.create_user(
            username="parent_user", password="password123"
        )  # nosec B106
        self.parent_adult = Adult.objects.create(
            user=self.parent_user, first_name="Parent", last_name="User", is_parent=True
        )

        # Create a Student
        self.student_user = User.objects.create_user(
            username="student_user", password="password123"
        )  # nosec B106
        self.student_profile = Student.objects.create(
            user=self.student_user, first_name="Student", last_name="User"
        )

        # Create programs
        today = timezone.now().date()
        self.active_program = Program.objects.create(
            name="Active Program",
            active=True,
            start_date=today - timedelta(days=10),
            end_date=today + timedelta(days=10),
        )
        self.past_program = Program.objects.create(
            name="Past Program",
            active=True,
            start_date=today - timedelta(days=30),
            end_date=today - timedelta(days=10),
        )
        self.upcoming_program = Program.objects.create(
            name="Upcoming Program",
            active=True,
            start_date=today + timedelta(days=10),
            end_date=today + timedelta(days=30),
        )
        self.inactive_program = Program.objects.create(
            name="Inactive Program",
            active=False,
            start_date=today - timedelta(days=10),
            end_date=today + timedelta(days=10),
        )

        # Ensure RolePermissions exist with defaults
        sections = RolePermission.SECTION_CHOICES
        roles = ["Mentor", "Parent", "Student"]
        for role in roles:
            for section_code, section_name in sections:
                RolePermission.objects.get_or_create(role=role, section=section_code)

    def test_student_cannot_view_program_detail(self):
        self.client.login(username="student_user", password="password123")  # nosec B106
        url = reverse("program_detail", args=[self.active_program.pk])
        response = self.client.get(url)
        # Should redirect to home/dashboard with error
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("home"))

    def test_parent_cannot_view_program_detail(self):
        self.client.login(username="parent_user", password="password123")  # nosec B106
        url = reverse("program_detail", args=[self.active_program.pk])
        response = self.client.get(url)
        # Should redirect to home/dashboard with error
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("home"))

    def test_mentor_can_view_active_program(self):
        self.client.login(username="mentor_user", password="password123")  # nosec B106
        url = reverse("program_detail", args=[self.active_program.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_mentor_cannot_view_past_program(self):
        self.client.login(username="mentor_user", password="password123")  # nosec B106
        url = reverse("program_detail", args=[self.past_program.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("home"))

    def test_mentor_cannot_view_upcoming_program(self):
        self.client.login(username="mentor_user", password="password123")  # nosec B106
        url = reverse("program_detail", args=[self.upcoming_program.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("home"))

    def test_mentor_cannot_view_inactive_program(self):
        self.client.login(username="mentor_user", password="password123")  # nosec B106
        url = reverse("program_detail", args=[self.inactive_program.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("home"))

    def test_lead_mentor_can_view_all_programs(self):
        self.client.login(username="lead_mentor", password="password123")  # nosec B106
        for prog in [
            self.active_program,
            self.past_program,
            self.upcoming_program,
            self.inactive_program,
        ]:
            url = reverse("program_detail", args=[prog.pk])
            response = self.client.get(url)
            self.assertEqual(
                response.status_code, 200, f"Lead mentor should view {prog.name}"
            )

    def test_mentor_restricted_actions_in_context(self):
        self.client.login(username="mentor_user", password="password123")  # nosec B106
        url = reverse("program_detail", args=[self.active_program.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        # Check context variables that should be False for mentors
        self.assertFalse(
            response.context.get("can_add_payment"), "Mentor should not add payments"
        )
        self.assertFalse(
            response.context.get("can_view_payments"), "Mentor should not view payments"
        )
        self.assertFalse(
            response.context.get("can_view_attendance"),
            "Mentor should not view attendance",
        )
        self.assertFalse(
            response.context.get("can_manage_fees"), "Mentor should not manage fees"
        )
        self.assertFalse(
            response.context.get("can_manage_students"),
            "Mentor should not manage students",
        )

    def test_program_list_filtered_for_mentor(self):
        self.client.login(username="mentor_user", password="password123")  # nosec B106
        url = reverse("program_list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        programs = response.context["programs"]
        self.assertIn(self.active_program, programs)
        self.assertNotIn(self.past_program, programs)
        self.assertNotIn(self.upcoming_program, programs)
        self.assertNotIn(self.inactive_program, programs)

    def test_program_list_empty_for_student(self):
        self.client.login(username="student_user", password="password123")  # nosec B106
        url = reverse("program_list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["programs"]), 0)
