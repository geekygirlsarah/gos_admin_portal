"""Program detail page shows a no-fees warning to Lead Mentors only."""

from datetime import timedelta

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from programs.models import Adult, Fee, Program

WARNING_TEXT = "No fees have been added to this program yet"


class ProgramNoFeesWarningTests(TestCase):
    def setUp(self):
        self.lead_user = User.objects.create_user(
            username="lead", password="password123"
        )  # nosec B106
        lm_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.lead_user.groups.add(lm_group)

        self.mentor_user = User.objects.create_user(
            username="mentor", password="password123"
        )  # nosec B106
        self.mentor_adult = Adult.objects.create(
            user=self.mentor_user,
            legal_first_name="Mentor",
            last_name="User",
            is_mentor=True,
        )

        today = timezone.now().date()
        self.program = Program.objects.create(
            name="No Fees Program",
            active=True,
            start_date=today - timedelta(days=10),
            end_date=today + timedelta(days=10),
        )

    def _get_program_detail(self, username):
        self.client.login(username=username, password="password123")  # nosec B106
        url = reverse("program_detail", args=[self.program.pk])
        return self.client.get(url)

    def test_warning_shown_for_lead_mentor_when_no_fees(self):
        response = self._get_program_detail("lead")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, WARNING_TEXT)

    def test_no_warning_for_lead_mentor_when_fees_present(self):
        Fee.objects.create(program=self.program, name="Registration", amount="100.00")
        response = self._get_program_detail("lead")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, WARNING_TEXT)

    def test_no_warning_for_mentor_when_no_fees(self):
        response = self._get_program_detail("mentor")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, WARNING_TEXT)


class ProgramNoFeesWarningRenderingTests(TestCase):
    """Verify the banner is a Bootstrap warning alert (not inline styled)."""

    def setUp(self):
        self.lead_user = User.objects.create_user(
            username="render_lead", password="password123"
        )  # nosec B106
        lm_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.lead_user.groups.add(lm_group)
        today = timezone.now().date()
        self.program = Program.objects.create(
            name="Render Program",
            active=True,
            start_date=today - timedelta(days=10),
            end_date=today + timedelta(days=10),
        )

    def test_warning_uses_bootstrap_alert_classes(self):
        self.client.login(username="render_lead", password="password123")  # nosec B106
        url = reverse("program_detail", args=[self.program.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "alert alert-warning")
        self.assertContains(response, WARNING_TEXT)
