from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from programs.models import Adult


class PortalDashboardTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser", password="password123"
        )  # nosec B106
        self.client.login(username="testuser", password="password123")  # nosec B106

    def test_dashboard_comments_not_rendered(self):
        url = reverse("profile_dashboard")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        # Check for the problematic strings
        # Since they are rendered as text if the comment tag fails,
        # we check if the literal '{#' and '#}' with the dashboard labels are present.

        problematic_strings = [
            "STUDENT DASHBOARD",
            "PARENT DASHBOARD",
            "MENTOR DASHBOARD",
            "ALUMNI DASHBOARD",
            "ACCOUNT INFO SIDEBAR",
        ]

        for s in problematic_strings:
            self.assertNotContains(response, f"{{#")
            self.assertNotContains(response, s)


class PortalDashboardContactBoxRemovalTests(TestCase):
    """The old 'Your Contact Information' box (name/phone/email) was removed."""

    def setUp(self):
        self.mentor = User.objects.create_user(
            username="mentor_contact", password="password123"
        )  # nosec B106
        Adult.objects.create(
            user=self.mentor,
            first_name="Mentor",
            last_name="User",
            is_mentor=True,
            phone_number="412-555-0100",
        )
        self.client.login(
            username="mentor_contact", password="password123"
        )  # nosec B106

    def test_dashboard_does_not_show_contact_information_box(self):
        response = self.client.get(reverse("profile_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Your Contact Information")
        self.assertNotContains(response, "To update your contact information")
