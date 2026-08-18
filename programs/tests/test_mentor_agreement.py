"""Tests for the Mentor Agreement feature (multi-agreement version).

Covers:
- MentorAgreement model (versioning, active toggle, get_active, get_all_active)
- MentorAgreementAcceptance model (recording, uniqueness, has_accepted_for_user)
- MentorAgreementSubmission model (signed document uploads)
- MentorAgreementMiddleware (redirect logic for mentors missing agreements)
- MentorAgreementView (multi-agreement GET, agree-all POST, disagree POST, upload)
- PortalAgreementView (agreement management)
"""

from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from programs.models import (
    Adult,
    MentorAgreement,
    MentorAgreementAcceptance,
    MentorAgreementSubmission,
)

# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


class MentorAgreementModelTests(TestCase):
    def setUp(self):
        self.agreement = MentorAgreement.objects.create(
            slug="data-access-policy",
            version=1,
            title="Data Access Policy v1",
            content="# Agreement\n\nYou agree to protect data.",
            effective_date="2025-01-01",
            is_active=True,
        )

    def test_get_active_returns_active_version(self):
        self.assertEqual(MentorAgreement.get_active(), self.agreement)

    def test_get_active_filters_by_slug(self):
        MentorAgreement.objects.create(
            slug="yearly-doc",
            version=1,
            title="Yearly Doc",
            content="Yearly content",
            effective_date="2025-01-01",
            is_active=True,
        )
        self.assertEqual(
            MentorAgreement.get_active(slug="data-access-policy"), self.agreement
        )
        yearly = MentorAgreement.get_active(slug="yearly-doc")
        self.assertEqual(yearly.title, "Yearly Doc")

    def test_get_active_returns_none_when_no_active(self):
        self.agreement.is_active = False
        self.agreement.save()
        self.assertIsNone(MentorAgreement.get_active())

    def test_get_all_active_returns_multiple(self):
        MentorAgreement.objects.create(
            slug="yearly-doc",
            version=1,
            title="Yearly Doc",
            content="Yearly content",
            effective_date="2025-01-01",
            is_active=True,
        )
        active = MentorAgreement.get_all_active()
        self.assertEqual(active.count(), 2)

    def test_get_all_active_only_returns_active(self):
        MentorAgreement.objects.create(
            slug="yearly-doc",
            version=1,
            title="Yearly Doc",
            content="Yearly content",
            effective_date="2025-01-01",
            is_active=False,
        )
        active = MentorAgreement.get_all_active()
        self.assertEqual(active.count(), 1)
        self.assertEqual(active.first(), self.agreement)

    def test_activating_new_version_deactivates_old_same_slug(self):
        v2 = MentorAgreement.objects.create(
            slug="data-access-policy",
            version=2,
            title="Data Access Policy v2",
            content="# Agreement v2\n\nUpdated terms.",
            effective_date="2025-06-01",
            is_active=True,
        )
        self.agreement.refresh_from_db()
        self.assertFalse(self.agreement.is_active)
        self.assertTrue(v2.is_active)
        self.assertEqual(MentorAgreement.get_active(slug="data-access-policy"), v2)

    def test_activating_different_slug_does_not_deactivate_other(self):
        MentorAgreement.objects.create(
            slug="yearly-doc",
            version=1,
            title="Yearly Doc",
            content="Yearly content",
            effective_date="2025-01-01",
            is_active=True,
        )
        self.agreement.refresh_from_db()
        self.assertTrue(self.agreement.is_active)

    def test_version_unique_per_slug(self):
        with self.assertRaises(Exception):
            MentorAgreement.objects.create(
                slug="data-access-policy",
                version=1,
                title="Duplicate",
                content="text",
                effective_date="2025-01-01",
                is_active=False,
            )

    def test_same_version_allowed_for_different_slugs(self):
        MentorAgreement.objects.create(
            slug="yearly-doc",
            version=1,
            title="Yearly Doc",
            content="Yearly content",
            effective_date="2025-01-01",
            is_active=False,
        )
        self.assertEqual(MentorAgreement.objects.filter(version=1).count(), 2)

    def test_str(self):
        self.assertEqual(str(self.agreement), "Data Access Policy v1 (v1)")

    def test_no_active_agreement_get_active_returns_none(self):
        MentorAgreement.objects.all().delete()
        self.assertIsNone(MentorAgreement.get_active())

    def test_updated_at_auto_set(self):
        self.assertIsNotNone(self.agreement.updated_at)


class MentorAgreementAcceptanceTests(TestCase):
    def setUp(self):
        self.agreement = MentorAgreement.objects.create(
            slug="data-access-policy",
            version=1,
            title="Policy v1",
            content="content",
            effective_date="2025-01-01",
            is_active=True,
        )
        self.user = User.objects.create_user(
            username="mentor1", password="password123"  # nosec B106
        )
        self.adult = Adult.objects.create(
            user=self.user,
            first_name="Test",
            last_name="Mentor",
            is_mentor=True,
        )

    def test_create_acceptance(self):
        acceptance = MentorAgreementAcceptance.objects.create(
            adult=self.adult,
            agreement=self.agreement,
        )
        self.assertEqual(acceptance.adult, self.adult)
        self.assertEqual(acceptance.agreement, self.agreement)
        self.assertIsNotNone(acceptance.accepted_at)

    def test_unique_together_constraint(self):
        MentorAgreementAcceptance.objects.create(
            adult=self.adult,
            agreement=self.agreement,
        )
        with self.assertRaises(Exception):
            MentorAgreementAcceptance.objects.create(
                adult=self.adult,
                agreement=self.agreement,
            )

    def test_different_versions_can_be_accepted(self):
        v2 = MentorAgreement.objects.create(
            slug="data-access-policy",
            version=2,
            title="Policy v2",
            content="updated",
            effective_date="2025-06-01",
            is_active=True,
        )
        MentorAgreementAcceptance.objects.create(
            adult=self.adult, agreement=self.agreement
        )
        acceptance_v2 = MentorAgreementAcceptance.objects.create(
            adult=self.adult, agreement=v2
        )
        self.assertIsNotNone(acceptance_v2)

    def test_different_slugs_can_be_accepted(self):
        yearly = MentorAgreement.objects.create(
            slug="yearly-doc",
            version=1,
            title="Yearly Doc",
            content="yearly",
            effective_date="2025-01-01",
            is_active=True,
        )
        MentorAgreementAcceptance.objects.create(
            adult=self.adult, agreement=self.agreement
        )
        acceptance_yearly = MentorAgreementAcceptance.objects.create(
            adult=self.adult, agreement=yearly
        )
        self.assertIsNotNone(acceptance_yearly)

    def test_has_accepted_current_agreement_true(self):
        MentorAgreementAcceptance.objects.create(
            adult=self.adult, agreement=self.agreement
        )
        self.assertTrue(self.adult.has_accepted_current_agreement())

    def test_has_accepted_current_agreement_false(self):
        self.assertFalse(self.adult.has_accepted_current_agreement())

    def test_has_accepted_current_agreement_false_for_stale(self):
        """Acceptance for old version doesn't count for new active version."""
        MentorAgreementAcceptance.objects.create(
            adult=self.adult, agreement=self.agreement
        )
        MentorAgreement.objects.create(
            slug="data-access-policy",
            version=2,
            title="Policy v2",
            content="updated",
            effective_date="2025-06-01",
            is_active=True,
        )
        self.adult.refresh_from_db()
        self.assertFalse(self.adult.has_accepted_current_agreement())

    def test_has_accepted_current_agreement_requires_all_slugs(self):
        """Must accept both active agreements across different slugs."""
        MentorAgreementAcceptance.objects.create(
            adult=self.adult, agreement=self.agreement
        )
        yearly = MentorAgreement.objects.create(
            slug="yearly-doc",
            version=1,
            title="Yearly Doc",
            content="yearly",
            effective_date="2025-01-01",
            is_active=True,
        )
        self.assertFalse(self.adult.has_accepted_current_agreement())
        MentorAgreementAcceptance.objects.create(adult=self.adult, agreement=yearly)
        self.assertTrue(self.adult.has_accepted_current_agreement())

    def test_has_accepted_current_agreement_false_no_active(self):
        """No active agreement means no acceptance needed (returns True)."""
        self.agreement.is_active = False
        self.agreement.save()
        self.assertTrue(self.adult.has_accepted_current_agreement())

    def test_has_accepted_current_agreement_false_no_adult_profile(self):
        """User with no adult profile returns False."""
        user_no_profile = User.objects.create_user(
            username="no_profile", password="password123"  # nosec B106
        )
        self.assertFalse(
            MentorAgreementAcceptance.has_accepted_for_user(user_no_profile)
        )

    def test_has_accepted_for_user_all_active(self):
        """has_accepted_for_user returns True only when ALL active are accepted."""
        MentorAgreementAcceptance.objects.create(
            adult=self.adult, agreement=self.agreement
        )
        yearly = MentorAgreement.objects.create(
            slug="yearly-doc",
            version=1,
            title="Yearly Doc",
            content="yearly",
            effective_date="2025-01-01",
            is_active=True,
        )
        self.assertFalse(MentorAgreementAcceptance.has_accepted_for_user(self.user))
        MentorAgreementAcceptance.objects.create(adult=self.adult, agreement=yearly)
        self.assertTrue(MentorAgreementAcceptance.has_accepted_for_user(self.user))


# ---------------------------------------------------------------------------
# Middleware tests
# ---------------------------------------------------------------------------


@override_settings(MENTOR_AGREEMENT_ENABLED=True)
class MentorAgreementMiddlewareTests(TestCase):
    def setUp(self):
        self.agreement = MentorAgreement.objects.create(
            slug="data-access-policy",
            version=1,
            title="Policy v1",
            content="content",
            effective_date="2025-01-01",
            is_active=True,
        )
        self.mentor = User.objects.create_user(
            username="mentor", password="password123"  # nosec B106
        )
        Adult.objects.create(
            user=self.mentor,
            first_name="Mentor",
            last_name="User",
            is_mentor=True,
        )

        self.parent_mentor = User.objects.create_user(
            username="parent_mentor", password="password123"  # nosec B106
        )
        Adult.objects.create(
            user=self.parent_mentor,
            first_name="Parent",
            last_name="Mentor",
            is_mentor=True,
            is_parent=True,
        )

        self.lead_mentor = User.objects.create_user(
            username="lead_mentor", password="password123"  # nosec B106
        )
        lead_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.lead_mentor.groups.add(lead_group)
        Adult.objects.create(
            user=self.lead_mentor,
            first_name="Lead",
            last_name="Mentor",
            is_mentor=True,
        )

        self.parent_only = User.objects.create_user(
            username="parent_only", password="password123"  # nosec B106
        )
        Adult.objects.create(
            user=self.parent_only,
            first_name="Parent",
            last_name="Only",
            is_parent=True,
        )

        self.student = User.objects.create_user(
            username="student", password="password123"  # nosec B106
        )
        from programs.models import Student

        Student.objects.create(
            user=self.student,
            legal_first_name="Student",
            last_name="User",
        )

    def test_mentor_without_agreement_redirected(self):
        self.client.force_login(self.mentor)
        response = self.client.get(reverse("profile_dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/mentor-agreement/", response.url)

    def test_parent_mentor_without_agreement_redirected(self):
        self.client.force_login(self.parent_mentor)
        response = self.client.get(reverse("profile_dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/mentor-agreement/", response.url)

    def test_lead_mentor_without_agreement_redirected(self):
        self.client.force_login(self.lead_mentor)
        response = self.client.get(reverse("profile_dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/mentor-agreement/", response.url)

    def test_mentor_with_agreement_not_redirected(self):
        MentorAgreementAcceptance.objects.create(
            adult=self.mentor.adult_profile, agreement=self.agreement
        )
        self.client.force_login(self.mentor)
        response = self.client.get(reverse("profile_dashboard"))
        self.assertEqual(response.status_code, 200)

    def test_parent_only_not_redirected(self):
        self.client.force_login(self.parent_only)
        response = self.client.get(reverse("profile_dashboard"))
        self.assertEqual(response.status_code, 200)

    def test_student_not_redirected(self):
        self.client.force_login(self.student)
        response = self.client.get(reverse("profile_dashboard"))
        self.assertEqual(response.status_code, 200)

    def test_agreement_page_accessible_without_agreement(self):
        self.client.force_login(self.mentor)
        response = self.client.get(reverse("mentor_agreement"))
        self.assertEqual(response.status_code, 200)

    def test_next_url_preserved_in_redirect(self):
        self.client.force_login(self.mentor)
        response = self.client.get(reverse("program_list"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/mentor-agreement/", response.url)
        self.assertIn("next=", response.url)

    def test_no_active_agreement_passes_through(self):
        self.agreement.is_active = False
        self.agreement.save()
        self.client.force_login(self.mentor)
        response = self.client.get(reverse("profile_dashboard"))
        self.assertEqual(response.status_code, 200)

    def test_unauthenticated_not_affected(self):
        response = self.client.get(reverse("profile_dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)

    def test_mentor_with_partial_agreements_redirected(self):
        """Mentor who accepted only one of two active agreements is redirected."""
        MentorAgreementAcceptance.objects.create(
            adult=self.mentor.adult_profile, agreement=self.agreement
        )
        MentorAgreement.objects.create(
            slug="yearly-doc",
            version=1,
            title="Yearly Doc",
            content="yearly",
            effective_date="2025-01-01",
            is_active=True,
        )
        self.client.force_login(self.mentor)
        response = self.client.get(reverse("profile_dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/mentor-agreement/", response.url)

    def test_mentor_with_all_agreements_not_redirected(self):
        """Mentor who accepted all active agreements passes through."""
        MentorAgreementAcceptance.objects.create(
            adult=self.mentor.adult_profile, agreement=self.agreement
        )
        yearly = MentorAgreement.objects.create(
            slug="yearly-doc",
            version=1,
            title="Yearly Doc",
            content="yearly",
            effective_date="2025-01-01",
            is_active=True,
        )
        MentorAgreementAcceptance.objects.create(
            adult=self.mentor.adult_profile, agreement=yearly
        )
        self.client.force_login(self.mentor)
        response = self.client.get(reverse("profile_dashboard"))
        self.assertEqual(response.status_code, 200)


@override_settings(MENTOR_AGREEMENT_ENABLED=False)
class MentorAgreementMiddlewareDisabledTests(TestCase):
    def test_mentor_passes_through_when_disabled(self):
        user = User.objects.create_user(
            username="mentor", password="password123"  # nosec B106
        )
        Adult.objects.create(
            user=user, first_name="Mentor", last_name="User", is_mentor=True
        )
        self.client.force_login(user)
        response = self.client.get(reverse("profile_dashboard"))
        self.assertEqual(response.status_code, 200)


# ---------------------------------------------------------------------------
# View tests
# ---------------------------------------------------------------------------


@override_settings(MENTOR_AGREEMENT_ENABLED=True)
class MentorAgreementViewTests(TestCase):
    def setUp(self):
        self.agreement = MentorAgreement.objects.create(
            slug="data-access-policy",
            version=1,
            title="Data Access Policy v1",
            content="# Agreement\n\nYou agree to protect data.",
            effective_date="2025-01-01",
            is_active=True,
        )
        self.mentor = User.objects.create_user(
            username="mentor", password="password123"  # nosec B106
        )
        self.adult = Adult.objects.create(
            user=self.mentor,
            first_name="Mentor",
            last_name="User",
            is_mentor=True,
        )

    def test_get_renders_pending_agreement(self):
        self.client.force_login(self.mentor)
        response = self.client.get(reverse("mentor_agreement"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Version 1")
        self.assertContains(response, "I Agree")

    def test_post_agree_creates_acceptance_and_redirects(self):
        self.client.force_login(self.mentor)
        response = self.client.post(reverse("mentor_agreement"), {"action": "agree"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/profile/")
        self.assertTrue(
            MentorAgreementAcceptance.objects.filter(
                adult=self.adult, agreement=self.agreement
            ).exists()
        )

    def test_post_agree_accepts_all_pending(self):
        """POST agree should accept all pending agreements at once."""
        yearly = MentorAgreement.objects.create(
            slug="yearly-doc",
            version=1,
            title="Yearly Doc",
            content="yearly content",
            effective_date="2025-01-01",
            is_active=True,
        )
        self.client.force_login(self.mentor)
        self.client.post(reverse("mentor_agreement"), {"action": "agree"})
        self.assertTrue(
            MentorAgreementAcceptance.objects.filter(
                adult=self.adult, agreement=self.agreement
            ).exists()
        )
        self.assertTrue(
            MentorAgreementAcceptance.objects.filter(
                adult=self.adult, agreement=yearly
            ).exists()
        )

    def test_post_agree_respects_next_param(self):
        self.client.force_login(self.mentor)
        response = self.client.post(
            reverse("mentor_agreement") + "?next=/programs/",
            {"action": "agree"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/programs/")

    def test_post_disagree_logs_out(self):
        self.client.force_login(self.mentor)
        response = self.client.post(reverse("mentor_agreement"), {"action": "disagree"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)
        response2 = self.client.get(reverse("profile_dashboard"))
        self.assertEqual(response2.status_code, 302)
        self.assertIn("/accounts/login/", response2.url)

    def test_post_disagree_shows_message(self):
        self.client.force_login(self.mentor)
        response = self.client.post(reverse("mentor_agreement"), {"action": "disagree"})
        login_response = self.client.get(response.url)
        self.assertContains(login_response, "must agree")

    def test_already_agreed_shows_success_message(self):
        MentorAgreementAcceptance.objects.create(
            adult=self.adult, agreement=self.agreement
        )
        self.client.force_login(self.mentor)
        response = self.client.get(reverse("mentor_agreement"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already accepted")

    def test_requires_login(self):
        """Agreement page is accessible to anonymous users (needed for middleware redirect target)."""
        response = self.client.get(reverse("mentor_agreement"))
        self.assertEqual(response.status_code, 200)

    def test_no_active_agreement_shows_message(self):
        self.agreement.is_active = False
        self.agreement.save()
        self.client.force_login(self.mentor)
        response = self.client.get(reverse("mentor_agreement"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "no active")

    def test_version_shown_in_template(self):
        self.client.force_login(self.mentor)
        response = self.client.get(reverse("mentor_agreement"))
        self.assertContains(response, "Version 1")

    def test_post_without_action_shows_page(self):
        self.client.force_login(self.mentor)
        response = self.client.post(reverse("mentor_agreement"), {})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "I Agree")

    def test_already_accepted_agreement_not_in_pending(self):
        """Accepted agreements should show as 'already accepted', not pending."""
        MentorAgreementAcceptance.objects.create(
            adult=self.adult, agreement=self.agreement
        )
        self.client.force_login(self.mentor)
        response = self.client.get(reverse("mentor_agreement"))
        self.assertContains(response, "already accepted")
        # The pending section should not show this agreement's content
        self.assertNotContains(
            response, "I have read and agree to the above Data Access Policy v1"
        )

    def test_multiple_pending_shows_all(self):
        """Multiple pending agreements are all shown."""
        MentorAgreement.objects.create(
            slug="yearly-doc",
            version=1,
            title="Yearly Mentor Agreement",
            content="yearly content",
            effective_date="2025-01-01",
            is_active=True,
        )
        self.client.force_login(self.mentor)
        response = self.client.get(reverse("mentor_agreement"))
        self.assertContains(response, "Data Access Policy v1")
        self.assertContains(response, "Yearly Mentor Agreement")

    def test_partial_acceptance_shows_remaining(self):
        """After accepting one agreement, only the other remains pending."""
        MentorAgreement.objects.create(
            slug="yearly-doc",
            version=1,
            title="Yearly Doc",
            content="yearly",
            effective_date="2025-01-01",
            is_active=True,
        )
        MentorAgreementAcceptance.objects.create(
            adult=self.adult, agreement=self.agreement
        )
        self.client.force_login(self.mentor)
        response = self.client.get(reverse("mentor_agreement"))
        self.assertContains(response, "Yearly Doc")
        self.assertNotContains(response, "Data Access Policy v1")


# ---------------------------------------------------------------------------
# Portal Settings agreement management tests
# ---------------------------------------------------------------------------


class PortalAgreementViewTests(TestCase):
    def setUp(self):
        self.lead = User.objects.create_user(
            username="lead", password="password123"  # nosec B106
        )
        lead_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.lead.groups.add(lead_group)
        Adult.objects.create(
            user=self.lead,
            first_name="Lead",
            last_name="Mentor",
            is_mentor=True,
        )
        self.client.force_login(self.lead)

    def test_add_agreement_creates_new(self):
        self.client.post(
            reverse("portal_agreements"),
            {
                "action": "add_agreement",
                "slug": "new-policy",
                "title": "New Policy",
                "content": "# New Policy\n\nContent here.",
                "effective_date": "2025-01-01",
                "is_active": "on",
            },
        )
        agreement = MentorAgreement.objects.get(slug="new-policy", version=1)
        self.assertEqual(agreement.title, "New Policy")
        self.assertTrue(agreement.is_active)

    def test_add_agreement_auto_versions(self):
        MentorAgreement.objects.create(
            slug="test-slug",
            version=1,
            title="Test v1",
            content="v1",
            effective_date="2025-01-01",
            is_active=True,
        )
        self.client.post(
            reverse("portal_agreements"),
            {
                "action": "add_agreement",
                "slug": "test-slug",
                "title": "Test v2",
                "content": "v2 content",
                "effective_date": "2025-06-01",
                "is_active": "on",
            },
        )
        v2 = MentorAgreement.objects.get(slug="test-slug", version=2)
        self.assertEqual(v2.title, "Test v2")

    def test_update_agreement_content_creates_new_version(self):
        agreement = MentorAgreement.objects.create(
            slug="test-slug",
            version=1,
            title="Test v1",
            content="old content",
            effective_date="2025-01-01",
            is_active=True,
        )
        self.client.post(
            reverse("portal_agreements"),
            {
                "action": "update_agreement",
                "agreement_id": agreement.id,
                "slug": "test-slug",
                "title": "Test v1 Updated",
                "content": "new content",
                "effective_date": "2025-01-01",
                "is_active": "on",
            },
        )
        v2 = MentorAgreement.objects.get(slug="test-slug", version=2)
        self.assertEqual(v2.content, "new content")
        self.assertTrue(v2.is_active)
        agreement.refresh_from_db()
        self.assertFalse(agreement.is_active)

    def test_update_metadata_does_not_create_version(self):
        agreement = MentorAgreement.objects.create(
            slug="test-slug",
            version=1,
            title="Test v1",
            content="content",
            effective_date="2025-01-01",
            is_active=True,
        )
        self.client.post(
            reverse("portal_agreements"),
            {
                "action": "update_agreement",
                "agreement_id": agreement.id,
                "slug": "test-slug",
                "title": "Test v1 (renamed)",
                "content": "content",
                "effective_date": "2025-01-01",
                "is_active": "on",
            },
        )
        self.assertEqual(MentorAgreement.objects.filter(slug="test-slug").count(), 1)
        agreement.refresh_from_db()
        self.assertEqual(agreement.title, "Test v1 (renamed)")

    def test_toggle_agreement(self):
        agreement = MentorAgreement.objects.create(
            slug="test-slug",
            version=1,
            title="Test",
            content="content",
            effective_date="2025-01-01",
            is_active=True,
        )
        self.client.post(
            reverse("portal_agreements"),
            {"action": "toggle_agreement", "agreement_id": agreement.id},
        )
        agreement.refresh_from_db()
        self.assertFalse(agreement.is_active)

    def test_delete_agreement_removes_all_versions(self):
        agreement = MentorAgreement.objects.create(
            slug="test-slug",
            version=1,
            title="Test",
            content="content",
            effective_date="2025-01-01",
            is_active=True,
        )
        MentorAgreement.objects.create(
            slug="test-slug",
            version=2,
            title="Test v2",
            content="v2",
            effective_date="2025-06-01",
            is_active=False,
        )
        self.client.post(
            reverse("portal_agreements"),
            {"action": "delete_agreement", "agreement_id": agreement.id},
        )
        self.assertEqual(MentorAgreement.objects.filter(slug="test-slug").count(), 0)

    def test_non_lead_cannot_access(self):
        self.client.logout()
        user = User.objects.create_user(
            username="regular", password="password123"  # nosec B106
        )
        Adult.objects.create(
            user=user, first_name="Regular", last_name="User", is_mentor=True
        )
        self.client.force_login(user)
        response = self.client.get("/programs/settings/?tab=agreements")
        self.assertIn(response.status_code, (403, 302))


# ---------------------------------------------------------------------------
# MentorAgreementSubmission model tests
# ---------------------------------------------------------------------------


class MentorAgreementSubmissionTests(TestCase):
    def setUp(self):
        self.agreement = MentorAgreement.objects.create(
            slug="data-access-policy",
            version=1,
            title="Data Access Policy v1",
            content="",
            document="agreements/policy.pdf",
            effective_date="2025-01-01",
            is_active=True,
        )
        self.user = User.objects.create_user(
            username="mentor1", password="password123"  # nosec B106
        )
        self.adult = Adult.objects.create(
            user=self.user,
            first_name="Test",
            last_name="Mentor",
            is_mentor=True,
        )

    def test_create_submission(self):
        submission = MentorAgreementSubmission.objects.create(
            adult=self.adult,
            agreement=self.agreement,
            file="signed/test.pdf",
        )
        self.assertEqual(submission.adult, self.adult)
        self.assertEqual(submission.agreement, self.agreement)
        self.assertIsNotNone(submission.uploaded_at)

    def test_unique_together_constraint(self):
        MentorAgreementSubmission.objects.create(
            adult=self.adult,
            agreement=self.agreement,
            file="signed/test.pdf",
        )
        with self.assertRaises(Exception):
            MentorAgreementSubmission.objects.create(
                adult=self.adult,
                agreement=self.agreement,
                file="signed/test2.pdf",
            )

    def test_different_adults_can_submit(self):
        user2 = User.objects.create_user(
            username="mentor2", password="password123"  # nosec B106
        )
        adult2 = Adult.objects.create(
            user=user2, first_name="Test2", last_name="Mentor2", is_mentor=True
        )
        MentorAgreementSubmission.objects.create(
            adult=self.adult, agreement=self.agreement, file="signed/test.pdf"
        )
        submission2 = MentorAgreementSubmission.objects.create(
            adult=adult2, agreement=self.agreement, file="signed/test2.pdf"
        )
        self.assertIsNotNone(submission2)

    def test_str(self):
        submission = MentorAgreementSubmission.objects.create(
            adult=self.adult, agreement=self.agreement, file="signed/test.pdf"
        )
        self.assertIn("Data Access Policy v1", str(submission))
        self.assertIn("Test Mentor", str(submission))


# ---------------------------------------------------------------------------
# View upload tests
# ---------------------------------------------------------------------------


@override_settings(MENTOR_AGREEMENT_ENABLED=True)
class MentorAgreementUploadViewTests(TestCase):
    def setUp(self):
        self.pdf_agreement = MentorAgreement.objects.create(
            slug="data-access-policy",
            version=1,
            title="Data Access Policy v1",
            content="# Policy\n\nPlease sign this document.",
            document="agreements/policy.pdf",
            effective_date="2025-01-01",
            is_active=True,
        )
        self.markdown_agreement = MentorAgreement.objects.create(
            slug="code-of-conduct",
            version=1,
            title="Code of Conduct",
            content="# Code of Conduct\n\nBe excellent to each other.",
            effective_date="2025-01-01",
            is_active=True,
        )
        self.mentor = User.objects.create_user(
            username="mentor", password="password123"  # nosec B106
        )
        self.adult = Adult.objects.create(
            user=self.mentor,
            first_name="Mentor",
            last_name="User",
            is_mentor=True,
        )

    def test_get_shows_upload_form_for_pdf_agreement(self):
        self.client.force_login(self.mentor)
        response = self.client.get(reverse("mentor_agreement"))
        self.assertContains(response, "Upload Signed Copy")
        self.assertContains(response, "Not yet uploaded")

    def test_get_shows_markdown_checkbox_not_upload(self):
        self.client.force_login(self.mentor)
        response = self.client.get(reverse("mentor_agreement"))
        self.assertContains(response, "Code of Conduct")
        # Markdown-only agreement should not show upload form;
        # the upload hidden field only appears for PDF agreements.
        # Count occurrences of the upload hidden field — should be 1 (pdf only)
        self.assertContains(response, "upload_agreement_id", 1)

    def test_upload_creates_submission(self):
        self.client.force_login(self.mentor)
        uploaded = SimpleUploadedFile("signed.pdf", b"%PDF-1.4 fake content")
        self.client.post(
            reverse("mentor_agreement"),
            {
                "action": "upload",
                "upload_agreement_id": self.pdf_agreement.id,
                "file_{}".format(self.pdf_agreement.id): uploaded,
            },
        )
        self.assertTrue(
            MentorAgreementSubmission.objects.filter(
                adult=self.adult, agreement=self.pdf_agreement
            ).exists()
        )

    def test_upload_redirects_back(self):
        self.client.force_login(self.mentor)
        uploaded = SimpleUploadedFile("signed.pdf", b"%PDF-1.4 fake content")
        response = self.client.post(
            reverse("mentor_agreement"),
            {
                "action": "upload",
                "upload_agreement_id": self.pdf_agreement.id,
                "file_{}".format(self.pdf_agreement.id): uploaded,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/mentor-agreement/", response.url)

    def test_upload_shows_success_message(self):
        self.client.force_login(self.mentor)
        uploaded = SimpleUploadedFile("signed.pdf", b"%PDF-1.4 fake content")
        self.client.post(
            reverse("mentor_agreement"),
            {
                "action": "upload",
                "upload_agreement_id": self.pdf_agreement.id,
                "file_{}".format(self.pdf_agreement.id): uploaded,
            },
        )
        response = self.client.get(reverse("mentor_agreement"))
        self.assertContains(response, "Signed copy uploaded")

    def test_upload_replaces_existing(self):
        self.client.force_login(self.mentor)
        uploaded1 = SimpleUploadedFile("signed1.pdf", b"%PDF-1.4 first")
        self.client.post(
            reverse("mentor_agreement"),
            {
                "action": "upload",
                "upload_agreement_id": self.pdf_agreement.id,
                "file_{}".format(self.pdf_agreement.id): uploaded1,
            },
        )
        uploaded2 = SimpleUploadedFile("signed2.pdf", b"%PDF-1.4 second")
        self.client.post(
            reverse("mentor_agreement"),
            {
                "action": "upload",
                "upload_agreement_id": self.pdf_agreement.id,
                "file_{}".format(self.pdf_agreement.id): uploaded2,
            },
        )
        self.assertEqual(
            MentorAgreementSubmission.objects.filter(
                adult=self.adult, agreement=self.pdf_agreement
            ).count(),
            1,
        )

    def test_agree_requires_upload_for_pdf_agreement(self):
        """Agree POST without upload for PDF agreement shows error."""
        self.client.force_login(self.mentor)
        response = self.client.post(reverse("mentor_agreement"), {"action": "agree"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Please upload signed copies")

    def test_agree_succeeds_after_upload(self):
        self.client.force_login(self.mentor)
        uploaded = SimpleUploadedFile("signed.pdf", b"%PDF-1.4 fake content")
        self.client.post(
            reverse("mentor_agreement"),
            {
                "action": "upload",
                "upload_agreement_id": self.pdf_agreement.id,
                "file_{}".format(self.pdf_agreement.id): uploaded,
            },
        )
        response = self.client.post(reverse("mentor_agreement"), {"action": "agree"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            MentorAgreementAcceptance.objects.filter(
                adult=self.adult, agreement=self.pdf_agreement
            ).exists()
        )
        self.assertTrue(
            MentorAgreementAcceptance.objects.filter(
                adult=self.adult, agreement=self.markdown_agreement
            ).exists()
        )

    def test_agree_markdown_only_does_not_require_upload(self):
        """Markdown-only agreements don't require uploads."""
        self.client.force_login(self.mentor)
        response = self.client.post(reverse("mentor_agreement"), {"action": "agree"})
        # Should error on PDF agreement, not markdown
        self.assertContains(response, "Please upload signed copies")

    def test_upload_shows_timestamp_after_upload(self):
        self.client.force_login(self.mentor)
        uploaded = SimpleUploadedFile("signed.pdf", b"%PDF-1.4 fake content")
        self.client.post(
            reverse("mentor_agreement"),
            {
                "action": "upload",
                "upload_agreement_id": self.pdf_agreement.id,
                "file_{}".format(self.pdf_agreement.id): uploaded,
            },
        )
        response = self.client.get(reverse("mentor_agreement"))
        self.assertContains(response, "Signed copy uploaded")

    def test_upload_enables_checkbox(self):
        """After upload, the checkbox for the PDF agreement should be enabled."""
        self.client.force_login(self.mentor)
        uploaded = SimpleUploadedFile("signed.pdf", b"%PDF-1.4 fake content")
        self.client.post(
            reverse("mentor_agreement"),
            {
                "action": "upload",
                "upload_agreement_id": self.pdf_agreement.id,
                "file_{}".format(self.pdf_agreement.id): uploaded,
            },
        )
        response = self.client.get(reverse("mentor_agreement"))
        # The checkbox should not be disabled after upload
        self.assertNotContains(
            response, 'id="agree-{}" disabled'.format(self.pdf_agreement.id)
        )

    def test_upload_without_file_shows_error(self):
        """Upload POST without a file should redirect with error message."""
        self.client.force_login(self.mentor)
        response = self.client.post(
            reverse("mentor_agreement"),
            {
                "action": "upload",
                "upload_agreement_id": self.pdf_agreement.id,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/mentor-agreement/", response.url)

    def test_upload_invalid_agreement_id(self):
        """Upload with invalid agreement_id shows error."""
        self.client.force_login(self.mentor)
        uploaded = SimpleUploadedFile("signed.pdf", b"%PDF-1.4 fake content")
        response = self.client.post(
            reverse("mentor_agreement"),
            {
                "action": "upload",
                "upload_agreement_id": 99999,
                "file_99999": uploaded,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/mentor-agreement/", response.url)
