"""TEMPORARY mentor access block (see settings.MENTOR_ACCESS_BLOCKED).

While the block is enabled:
- Users whose ONLY role is Mentor (no Parent/Alumni flags and not a lead
  mentor) cannot log in: their session is invalidated and they are sent back
  to the login page with a message.
- Users who are Mentors AND Parents/Alumni stay logged in but their mentor
  role is suppressed: get_user_role resolves to Parent/Alumni instead of
  Mentor and the dashboard hides the mentor section.
- Lead mentors and superusers are unaffected.

Remove this test file together with the temporary block.
"""

from django.conf import settings
from django.contrib.auth.models import Group, User
from django.test import TestCase, override_settings
from django.urls import reverse

from programs.models import Adult


@override_settings(MENTOR_ACCESS_BLOCKED=True)
class TemporaryMentorBlockTests(TestCase):
    def setUp(self):
        self.mentor_only_user = User.objects.create_user(
            username="mentor_only", password="password123"
        )  # nosec B106
        Adult.objects.create(
            user=self.mentor_only_user,
            first_name="Mentor",
            last_name="Only",
            is_mentor=True,
        )

        self.parent_mentor_user = User.objects.create_user(
            username="parent_mentor", password="password123"
        )  # nosec B106
        Adult.objects.create(
            user=self.parent_mentor_user,
            first_name="Parent",
            last_name="Mentor",
            is_mentor=True,
            is_parent=True,
        )

        self.alumni_mentor_user = User.objects.create_user(
            username="alumni_mentor", password="password123"
        )  # nosec B106
        Adult.objects.create(
            user=self.alumni_mentor_user,
            first_name="Alumni",
            last_name="Mentor",
            is_mentor=True,
            is_alumni=True,
        )

        self.lead_mentor_user = User.objects.create_user(
            username="lead_mentor", password="password123"
        )  # nosec B106
        lead_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.lead_mentor_user.groups.add(lead_group)

        self.superuser = User.objects.create_superuser(
            username="admin", password="password123"
        )  # nosec B106

    # ── get_user_role is mentor-aware while the block is on ────────────────

    def test_get_user_role_mentor_only_is_none(self):
        from programs.permission_views import get_user_role

        self.assertIsNone(get_user_role(self.mentor_only_user))

    def test_get_user_role_mentor_parent_resolves_to_parent(self):
        from programs.permission_views import get_user_role

        self.assertEqual(get_user_role(self.parent_mentor_user), "Parent")

    def test_get_user_role_mentor_alumni_resolves_to_alumni(self):
        from programs.permission_views import get_user_role

        self.assertEqual(get_user_role(self.alumni_mentor_user), "Alumni")

    def test_get_user_role_lead_mentor_still_lead_mentor(self):
        from programs.permission_views import get_user_role

        self.assertEqual(get_user_role(self.lead_mentor_user), "LeadMentor")

    def test_get_user_role_superuser_still_lead_mentor(self):
        from programs.permission_views import get_user_role

        self.assertEqual(get_user_role(self.superuser), "LeadMentor")

    def test_get_user_role_mentor_group_fallback_suppressed(self):
        from programs.permission_views import get_user_role

        user = User.objects.create_user(
            username="mentor_group", password="password123"
        )  # nosec B106
        group, _ = Group.objects.get_or_create(name="Mentor")
        user.groups.add(group)
        self.assertIsNone(get_user_role(user))

    def test_mentor_access_blocked_helper(self):
        from programs.permission_views import mentor_access_blocked

        self.assertTrue(mentor_access_blocked(self.mentor_only_user))
        self.assertTrue(mentor_access_blocked(self.parent_mentor_user))
        self.assertTrue(mentor_access_blocked(self.alumni_mentor_user))
        self.assertFalse(mentor_access_blocked(self.lead_mentor_user))
        self.assertFalse(mentor_access_blocked(self.superuser))

    # ── Mentor-only users are logged out with a message ────────────────────

    def test_mentor_only_user_is_logged_out_and_sees_message(self):
        self.client.force_login(self.mentor_only_user)
        response = self.client.get(reverse("profile_dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(settings.LOGIN_URL))

        login_response = self.client.get(response.url)
        self.assertContains(login_response, "Sorry, mentors cannot log in yet.")

        # Session was invalidated: a protected page now redirects to login.
        protected = self.client.get(reverse("profile_dashboard"))
        self.assertEqual(protected.status_code, 302)
        self.assertTrue(protected.url.startswith(settings.LOGIN_URL))

    # ── Parents/Alumni who also mentor can log in ──────────────────────────

    def test_parent_mentor_can_log_in_but_mentor_section_hidden(self):
        self.client.force_login(self.parent_mentor_user)
        response = self.client.get(reverse("profile_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["is_parent"])
        self.assertFalse(response.context["is_mentor"])
        self.assertContains(response, "My Students")
        self.assertNotContains(response, "View Roster &amp; Details")
        self.assertNotContains(response, "No active programs found at this time.")

    def test_alumni_mentor_can_log_in_but_mentor_section_hidden(self):
        self.client.force_login(self.alumni_mentor_user)
        response = self.client.get(reverse("profile_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["is_alumni"])
        self.assertFalse(response.context["is_mentor"])

    def test_lead_mentor_can_log_in(self):
        self.client.force_login(self.lead_mentor_user)
        response = self.client.get(reverse("profile_dashboard"))
        self.assertEqual(response.status_code, 200)

    def test_superuser_can_log_in(self):
        self.client.force_login(self.superuser)
        response = self.client.get(reverse("profile_dashboard"))
        self.assertEqual(response.status_code, 200)


@override_settings(MENTOR_ACCESS_BLOCKED=False)
class TemporaryMentorBlockDisabledTests(TestCase):
    def test_mentor_can_log_in_when_block_is_off(self):
        user = User.objects.create_user(
            username="mentor", password="password123"
        )  # nosec B106
        Adult.objects.create(
            user=user, first_name="Mentor", last_name="User", is_mentor=True
        )
        self.client.force_login(user)
        response = self.client.get(reverse("profile_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["is_mentor"])

    def test_get_user_role_mentor_when_block_is_off(self):
        from programs.permission_views import get_user_role

        user = User.objects.create_user(
            username="mentor", password="password123"
        )  # nosec B106
        Adult.objects.create(
            user=user, first_name="Mentor", last_name="User", is_mentor=True
        )
        self.assertEqual(get_user_role(user), "Mentor")
