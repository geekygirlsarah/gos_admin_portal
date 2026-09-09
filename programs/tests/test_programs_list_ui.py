from datetime import date, timedelta

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from programs.models import Adult, Enrollment, Program, ProgramFeature, Student


class ProgramListViewUITests(TestCase):
    """Test suite for the updated `/programs` list view UI, metrics, and permissions."""

    def setUp(self):
        self.lead_mentor_user = User.objects.create_user(
            username="lead_mentor", password="password123"  # nosec B106
        )
        lm_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.lead_mentor_user.groups.add(lm_group)

        self.mentor_user = User.objects.create_user(
            username="mentor_user", password="password123"  # nosec B106
        )
        self.mentor_adult = Adult.objects.create(
            user=self.mentor_user,
            legal_first_name="Mentor",
            last_name="User",
            is_mentor=True,
        )

        today = date.today()
        # Active program
        self.active_prog = Program.objects.create(
            name="Girls of Steel FRC",
            active=True,
            start_date=today - timedelta(days=30),
            end_date=today + timedelta(days=60),
            grade_range_start=9,
            grade_range_end=12,
        )
        # Upcoming program
        self.upcoming_prog = Program.objects.create(
            name="Girls of Steel FTC",
            active=True,
            start_date=today + timedelta(days=15),
            end_date=today + timedelta(days=120),
            grade_range_start=7,
            grade_range_end=8,
        )
        # Past program
        self.past_prog = Program.objects.create(
            name="Girls of Steel FLL 2025",
            active=True,
            start_date=today - timedelta(days=400),
            end_date=today - timedelta(days=200),
            grade_range_start=4,
            grade_range_end=6,
        )
        # Inactive program
        self.inactive_prog = Program.objects.create(
            name="Archived Legacy Program",
            active=False,
            start_date=today - timedelta(days=800),
            end_date=today - timedelta(days=600),
        )

        # Create students and enrollments
        self.student1 = Student.objects.create(
            legal_first_name="Alice", last_name="Smith", graduated=False
        )
        self.student2 = Student.objects.create(
            legal_first_name="Bob", last_name="Jones", graduated=False
        )
        self.student_grad = Student.objects.create(
            legal_first_name="Grace", last_name="Hopper", graduated=True
        )

        Enrollment.objects.create(
            student=self.student1, program=self.active_prog, active=True
        )
        Enrollment.objects.create(
            student=self.student2, program=self.active_prog, active=True
        )
        Enrollment.objects.create(
            student=self.student_grad, program=self.active_prog, active=True
        )

    def test_lead_mentor_sees_all_programs_and_metrics(self):
        self.client.login(username="lead_mentor", password="password123")  # nosec B106
        response = self.client.get(reverse("program_list"))
        self.assertEqual(response.status_code, 200)

        # Context metrics
        self.assertEqual(response.context["active_programs_count"], 1)
        self.assertEqual(response.context["upcoming_programs_count"], 1)
        self.assertEqual(
            response.context["past_programs_count"], 2
        )  # past_prog + inactive_prog
        self.assertEqual(
            response.context["total_enrolled_active_students"], 2
        )  # student1 & student2

        # Page contains key sections and program names
        self.assertContains(response, "Girls of Steel FRC")
        self.assertContains(response, "Girls of Steel FTC")
        self.assertContains(response, "Girls of Steel FLL 2025")
        self.assertContains(response, "Archived Legacy Program")
        self.assertContains(response, "Total Active Programs")
        self.assertContains(response, "Active Students")

    def test_mentor_sees_active_and_upcoming_clickable_and_past_unlinked(self):
        self.client.login(username="mentor_user", password="password123")  # nosec B106
        response = self.client.get(reverse("program_list"))
        self.assertEqual(response.status_code, 200)

        # Active & Upcoming programs clickable
        self.assertContains(response, f'href="/programs/{self.active_prog.id}/"')
        self.assertContains(response, f'href="/programs/{self.upcoming_prog.id}/"')

        # Past program present but not clickable
        self.assertContains(response, "Girls of Steel FLL 2025")
        self.assertNotContains(response, f'href="/programs/{self.past_prog.id}/"')

        # Inactive program completely hidden for mentors
        self.assertNotContains(response, "Archived Legacy Program")

    def test_feature_badges_and_actions_rendered(self):
        feat, _ = ProgramFeature.objects.get_or_create(
            key="outreach", defaults={"name": "Outreach"}
        )
        self.active_prog.features.add(feat)

        self.client.login(username="lead_mentor", password="password123")  # nosec B106
        response = self.client.get(reverse("program_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Outreach")
        self.assertContains(response, "Roster")

    def test_upcoming_program_card_actions(self):
        """Upcoming programs should show 'Open Program' without Roster or Message buttons."""
        self.client.login(username="mentor_user", password="password123")  # nosec B106
        response = self.client.get(reverse("program_list"))
        self.assertEqual(response.status_code, 200)

        # Active program has Open Program, Roster, and Message
        self.assertContains(
            response,
            reverse("program_assignment", args=[self.active_prog.id]),
        )
        self.assertContains(
            response,
            reverse("program_email", args=[self.active_prog.id]),
        )

        # Upcoming program does NOT have Open Setup, Roster, or Message buttons
        self.assertNotContains(response, "Open Setup")
        self.assertNotContains(
            response,
            reverse("program_assignment", args=[self.upcoming_prog.id]),
        )
        self.assertNotContains(
            response,
            reverse("program_email", args=[self.upcoming_prog.id]),
        )
        # Upcoming program has Open Program button
        self.assertContains(
            response,
            f'href="/programs/{self.upcoming_prog.id}/"',
        )
        self.assertContains(response, "Open Program")
