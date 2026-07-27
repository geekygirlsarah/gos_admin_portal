"""Reproduction test for the two-group Lead Mentor gap.

Before the fix: A user in the "LeadMentor" group (used throughout programs/)
could NOT access application review pages because those pages required the
separate "Lead Mentors" group (used only in applications/).

After the fix: There is a single "LeadMentor" group that grants both roles.
A member of "LeadMentor" can access application review pages because the
review_application permission is now granted to that one group.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from applications.models import Application

User = get_user_model()

LEAD_MENTOR_GROUP = "LeadMentor"
REVIEW_PERM_CODENAME = "review_application"


def _make_submitted_application():
    return Application.objects.create(
        applicant_type=Application.Type.PARENT,
        email="parent@example.com",
        current_step=8,
        email_verified_at=timezone.now(),
        status=Application.Status.SUBMITTED,
        submitted_at=timezone.now(),
        data={
            "step5-student": {
                "legal_first_name": "Ada",
                "last_name": "Lovelace",
                "email": "ada@example.com",
            },
            "step7-primaryparent": {
                "first_name": "Pat",
                "last_name": "Parent",
                "email": "parent@example.com",
            },
        },
    )


def _ensure_review_perm():
    from django.contrib.contenttypes.models import ContentType

    ct, _ = ContentType.objects.get_or_create(
        app_label="applications", model="application"
    )
    perm, _ = Permission.objects.get_or_create(
        content_type=ct,
        codename=REVIEW_PERM_CODENAME,
        defaults={"name": "Can review applications"},
    )
    return perm


class LeadMentorGroupMergeTests(TestCase):
    """After the group merge, the single 'LeadMentor' group grants
    access to the application review pages."""

    def setUp(self):
        self.app = _make_submitted_application()
        self.list_url = reverse("application_review_list")
        self.detail_url = reverse(
            "application_review_detail",
            kwargs={"app_id": self.app.application_id},
        )
        # Ensure the LeadMentor group exists with the review permission
        # (simulates what the migration does)
        self.lead_mentor_group, _ = Group.objects.get_or_create(name=LEAD_MENTOR_GROUP)
        self.lead_mentor_group.permissions.add(_ensure_review_perm())

    def test_lead_mentor_group_has_review_permission(self):
        """The unified 'LeadMentor' group must carry the review_application perm."""
        perm_codenames = set(
            self.lead_mentor_group.permissions.values_list("codename", flat=True)
        )
        self.assertIn(
            REVIEW_PERM_CODENAME,
            perm_codenames,
            "The 'LeadMentor' group is missing the 'review_application' permission.",
        )

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_lead_mentor_group_member_can_access_review_list(self):
        """A user in the 'LeadMentor' group can reach the application review list."""
        user = User.objects.create_user(username="lm_user", email="lm@x.test")
        user.groups.add(self.lead_mentor_group)
        self.client.force_login(user)

        response = self.client.get(self.list_url)
        self.assertEqual(
            response.status_code,
            200,
            "Expected LeadMentor group member to access review list (got "
            f"{response.status_code}).",
        )

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_lead_mentor_group_member_can_access_review_detail(self):
        """A user in the 'LeadMentor' group can reach the application review detail."""
        user = User.objects.create_user(username="lm_user2", email="lm2@x.test")
        user.groups.add(self.lead_mentor_group)
        self.client.force_login(user)

        response = self.client.get(self.detail_url)
        self.assertEqual(
            response.status_code,
            200,
            "Expected LeadMentor group member to access review detail (got "
            f"{response.status_code}).",
        )

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_old_lead_mentors_group_no_longer_exists(self):
        """After the migration, the old 'Lead Mentors' group (with space)
        should not exist — it should have been renamed to 'LeadMentor'."""
        self.assertFalse(
            Group.objects.filter(name="Lead Mentors").exists(),
            "The deprecated 'Lead Mentors' group still exists; "
            "it should have been renamed to 'LeadMentor'.",
        )

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_user_without_group_cannot_access_review(self):
        """A plain user without LeadMentor group cannot access review pages."""
        plain = User.objects.create_user(username="plain_user", email="plain@x.test")
        self.client.force_login(plain)

        response = self.client.get(self.list_url)
        self.assertIn(
            response.status_code,
            [302, 403],
            "Plain user should not be able to access review pages.",
        )
