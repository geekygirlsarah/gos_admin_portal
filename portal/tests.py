from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse


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
