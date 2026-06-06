from datetime import timedelta

from django.contrib.auth.models import Permission, User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from applications.models import Application


class StaleApplicationCleanupTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="reviewer", password="password")
        # Assign review permission
        perm = Permission.objects.get(codename="review_application")
        self.user.user_permissions.add(perm)
        self.client.login(username="reviewer", password="password")

        self.url = reverse("application_cleanup_stale")

    def test_cleanup_deletes_only_stale_applications(self):
        # Create a fresh application (today)
        fresh_app = Application.objects.create(
            application_id="FRESHAPP", email="fresh@example.com"
        )

        # Create a stale application (31 days ago)
        stale_app = Application.objects.create(
            application_id="STALEAPP", email="stale@example.com"
        )
        Application.objects.filter(pk=stale_app.pk).update(
            created_at=timezone.now() - timedelta(days=31)
        )

        # Verify both exist
        self.assertEqual(Application.objects.count(), 2)

        # Trigger cleanup
        response = self.client.post(self.url)

        # Redirect back to list
        self.assertRedirects(response, reverse("application_review_list"))

        # Verify only fresh app remains
        self.assertEqual(Application.objects.count(), 1)
        self.assertTrue(Application.objects.filter(pk=fresh_app.pk).exists())
        self.assertFalse(Application.objects.filter(pk=stale_app.pk).exists())

    def test_cleanup_requires_permission(self):
        self.client.logout()
        # Non-privileged user
        User.objects.create_user(username="random", password="password")
        self.client.login(username="random", password="password")

        response = self.client.post(self.url)
        # Should be forbidden (403) for logged-in user without permission
        self.assertEqual(response.status_code, 403)
