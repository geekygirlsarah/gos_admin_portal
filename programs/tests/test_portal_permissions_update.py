from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from programs.models import Adult, RolePermission


class PortalPermissionsUpdateTests(TestCase):
    def setUp(self):
        self.password = "password123"  # nosec B105

        # Lead Mentor
        self.lead_mentor = User.objects.create_user(
            username="lead_mentor_user", password=self.password
        )
        self.lead_mentor_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.lead_mentor.groups.add(self.lead_mentor_group)

        # Regular Mentor
        self.mentor_user = User.objects.create_user(
            username="mentor_user_perm", password=self.password
        )
        Adult.objects.create(
            user=self.mentor_user, first_name="Mentor", last_name="User", is_mentor=True
        )
        self.mentor_group, _ = Group.objects.get_or_create(name="Mentor")
        self.mentor_user.groups.add(self.mentor_group)

        self.url = reverse("portal_permissions_update")

    def test_lead_mentor_can_update_permissions(self):
        self.client.login(username="lead_mentor_user", password=self.password)

        perm, _ = RolePermission.objects.get_or_create(
            role="Mentor", section="attendance"
        )
        # Force initial state
        perm.can_read = False
        perm.can_write = False
        perm.save()

        # POST to update: enable read, leave write off
        response = self.client.post(
            self.url,
            {
                f"read_{perm.id}": "on",
                # NOT sending write_{perm.id}
            },
        )

        self.assertEqual(response.status_code, 302)
        perm.refresh_from_db()
        self.assertTrue(perm.can_read)
        self.assertFalse(perm.can_write)

        # POST again to update: enable both
        response = self.client.post(
            self.url,
            {
                f"read_{perm.id}": "on",
                f"write_{perm.id}": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        perm.refresh_from_db()
        self.assertTrue(perm.can_read)
        self.assertTrue(perm.can_write)

    def test_non_lead_mentor_cannot_update_permissions(self):
        self.client.login(username="mentor_user_perm", password=self.password)

        perm, _ = RolePermission.objects.get_or_create(
            role="Mentor", section="attendance"
        )
        perm.can_read = False
        perm.save()

        response = self.client.post(
            self.url,
            {
                f"read_{perm.id}": "on",
            },
        )

        # Should be redirected to home (due to LeadMentorRequiredMixin)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("home"))

        perm.refresh_from_db()
        self.assertFalse(perm.can_read)
