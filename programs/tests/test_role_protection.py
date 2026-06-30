from django.contrib.auth.models import Group, Permission, User
from django.test import TestCase
from django.urls import reverse

from programs.models import Adult, RolePermission


class RoleProtectionTests(TestCase):
    def setUp(self):
        # Create a regular mentor
        self.mentor_user = User.objects.create_user(
            username="mentor", password="pass"
        )  # nosec B106
        self.mentor_profile = Adult.objects.create(
            user=self.mentor_user,
            first_name="Regular",
            last_name="Mentor",
            is_mentor=True,
        )

        # Give them the Django permission required by the URL decorator
        perm = Permission.objects.get(codename="change_adult")
        self.mentor_user.user_permissions.add(perm)

        # Create a lead mentor
        self.lead_user = User.objects.create_superuser(
            username="lead", password="pass"
        )  # nosec B106

        # Grant regular mentors write access to adult_info
        RolePermission.objects.update_or_create(
            role="Mentor",
            section="adult_info",
            defaults={"can_read": True, "can_write": True},
        )

        self.client.login(username="mentor", password="pass")  # nosec B106

    def test_mentor_cannot_uncheck_is_mentor_flag(self):
        url = reverse("adult_edit", args=[self.mentor_profile.pk])

        # Try to uncheck is_mentor and is_parent (which is already False)
        data = {
            "first_name": "Regular",
            "last_name": "Mentor Updated",
            "personal_email": "mentor@example.com",
            # is_mentor and is_parent omitted, which in a standard form would mean False
        }

        self.client.login(username="mentor", password="pass")  # nosec B106
        resp = self.client.post(url, data)
        self.assertEqual(resp.status_code, 302)

        self.mentor_profile.refresh_from_db()
        self.assertEqual(self.mentor_profile.last_name, "Mentor Updated")

        # Flags should be preserved
        self.assertTrue(
            self.mentor_profile.is_mentor,
            "Mentor should NOT be able to change their own is_mentor flag",
        )
        self.assertTrue(
            self.mentor_profile.active,
            "Mentor should NOT be able to change their own active flag",
        )

    def test_parent_cannot_change_students(self):
        # Create a parent
        parent_user = User.objects.create_user(
            username="parent_user", password="pass"
        )  # nosec B106
        parent_profile = Adult.objects.create(
            user=parent_user, first_name="Parent", last_name="User", is_parent=True
        )
        # Give them the Django permission
        perm = Permission.objects.get(codename="change_adult")
        parent_user.user_permissions.add(perm)

        # Grant parents write access to adult_info
        RolePermission.objects.update_or_create(
            role="Parent",
            section="adult_info",
            defaults={"can_read": True, "can_write": True},
        )

        self.client.login(username="parent_user", password="pass")  # nosec B106
        url = reverse("parent_edit", args=[parent_profile.pk])

        data = {
            "first_name": "Parent",
            "last_name": "Updated",
            "personal_email": "parent@example.com",
            "students": [1, 2, 3],  # Try to change students
        }

        resp = self.client.post(url, data)
        self.assertEqual(resp.status_code, 302)

        parent_profile.refresh_from_db()
        self.assertEqual(parent_profile.last_name, "Updated")
        self.assertEqual(
            parent_profile.students.count(),
            0,
            "Parent should NOT be able to change students list",
        )

    def test_lead_mentor_CAN_change_flags(self):
        self.client.login(username="lead", password="pass")  # nosec B106
        url = reverse("adult_edit", args=[self.mentor_profile.pk])

        data = {
            "first_name": "Regular",
            "last_name": "Mentor",
            "personal_email": "mentor@example.com",
            "is_mentor": "on",
            "is_parent": "on",  # Lead mentor adds parent flag
            "active": "on",
        }

        resp = self.client.post(url, data)
        self.assertEqual(resp.status_code, 302)

        self.mentor_profile.refresh_from_db()
        self.assertTrue(
            self.mentor_profile.is_parent, "Lead mentor SHOULD be able to change flags"
        )
