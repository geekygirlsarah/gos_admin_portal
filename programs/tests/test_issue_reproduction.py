from django.contrib.auth.models import Permission, User
from django.test import TestCase
from django.urls import reverse

from programs.models import Adult


class IssueReproductionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username="admin", password="pass"
        )  # nosec B106
        self.client.login(username="admin", password="pass")  # nosec B106

        self.parent = Adult.objects.create(
            first_name="Original",
            last_name="Parent",
            personal_email="parent@example.com",
            is_parent=True,
            email_updates=True,
        )

    def test_parent_edit_preserves_flags(self):
        url = reverse("parent_edit", args=[self.parent.pk])

        # We simulate what the browser would send from parents/form.html
        # Missing: is_parent, active, personal_email (if we use 'email' which we just fixed)
        data = {
            "first_name": "Updated",
            "last_name": "Parent",
            "personal_email": "updated@example.com",
            "email_updates": "on",  # checkbox checked
        }

        resp = self.client.post(url, data)
        self.assertEqual(resp.status_code, 302)  # Should redirect on success

        self.parent.refresh_from_db()

        self.assertEqual(self.parent.first_name, "Updated")
        self.assertEqual(self.parent.personal_email, "updated@example.com")
        self.assertTrue(self.parent.is_parent, "is_parent flag should be preserved")
        self.assertTrue(
            self.parent.email_updates, "email_updates flag should be preserved"
        )
        self.assertTrue(self.parent.active, "active flag should be preserved")
