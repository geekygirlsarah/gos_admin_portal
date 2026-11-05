from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.db import IntegrityError, transaction
from django.http import Http404
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from audit.models import AuditLog
from organizations.models import Organization, UserOrganizationMembership
from programs.models import (
    Adult,
    MentorAgreement,
    MentorAgreementAcceptance,
    MentorAgreementSubmission,
)
from programs.views.portal_settings import PortalAgreementView, PortalSettingsView


@override_settings(ALLOWED_HOSTS=["testserver", "other.localhost"])
class TenantAgreementSettingsTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.get(slug="gos")
        self.other_org = Organization.objects.create(name="Other", slug="other")
        self.lead = get_user_model().objects.create_user(username="tenant-lead")
        UserOrganizationMembership.objects.create(
            user=self.lead, organization=self.org, role="LeadMentor"
        )
        UserOrganizationMembership.objects.create(
            user=self.lead, organization=self.other_org, role="LeadMentor"
        )
        self.client.force_login(self.lead)
        self.local = self.make_agreement(self.org, "shared", 1, "Local agreement")
        self.foreign = self.make_agreement(
            self.other_org, "shared", 5, "Foreign agreement"
        )
        self.foreign_only = self.make_agreement(
            self.other_org, "foreign-only", 1, "Foreign only agreement"
        )

    def make_agreement(self, organization, slug, version, title, active=True):
        return MentorAgreement.objects.create(
            organization=organization,
            slug=slug,
            version=version,
            title=title,
            content="Original content",
            effective_date="2025-01-01",
            is_active=active,
        )

    def agreement_data(self, action, agreement=None, **changes):
        data = {
            "action": action,
            "slug": "shared",
            "title": "Updated title",
            "content": "Updated content",
            "effective_date": "2025-01-01",
            "is_active": "on",
        }
        if agreement is not None:
            data["agreement_id"] = agreement.pk
        data.update(changes)
        return data

    def test_settings_lists_only_the_current_organizations_agreements_and_slugs(self):
        local_only = self.make_agreement(
            self.org, "local-only", 1, "Local only agreement"
        )
        self.make_agreement(self.org, "shared", 2, "Local agreement v2", active=False)
        response = self.client.get(reverse("portal_settings"), {"tab": "agreements"})
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.local, response.context["mentor_agreements"])
        self.assertNotIn(self.foreign, response.context["mentor_agreements"])
        self.assertNotIn(self.foreign_only, response.context["mentor_agreements"])
        self.assertIn(local_only, response.context["mentor_agreements"])
        self.assertIn("shared", response.context["mentor_agreement_slugs"])
        self.assertNotIn("foreign-only", response.context["mentor_agreement_slugs"])
        self.assertNotContains(response, "Foreign only agreement")

        response = self.client.get(
            reverse("portal_settings"),
            {"tab": "agreements"},
            HTTP_HOST="other.localhost",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.foreign, response.context["mentor_agreements"])
        self.assertNotIn(self.local, response.context["mentor_agreements"])
        self.assertNotIn("local-only", response.context["mentor_agreement_slugs"])

    def test_add_uses_local_version_and_explicit_organization(self):
        response = self.client.post(
            reverse("portal_agreements"), self.agreement_data("add_agreement")
        )
        self.assertEqual(response.status_code, 302)
        version = MentorAgreement.objects.get(
            organization=self.org, slug="shared", version=2
        )
        self.assertEqual(version.content, "Updated content")
        self.local.refresh_from_db()
        self.foreign.refresh_from_db()
        self.assertFalse(self.local.is_active)
        self.assertTrue(self.foreign.is_active)

    def test_unowned_agreement_is_not_listed_or_mutated(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            unowned = self.make_agreement(self.org, "unowned", 1, "Unowned agreement")
            MentorAgreement.objects.filter(pk=unowned.pk).update(organization=None)
        response = self.client.get(reverse("portal_settings"), {"tab": "agreements"})
        self.assertNotContains(response, "Unowned agreement")
        self.assertNotIn("unowned", response.context["mentor_agreement_slugs"])
        response = self.client.post(
            reverse("portal_agreements"),
            self.agreement_data("toggle_agreement", unowned),
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(MentorAgreement.objects.filter(pk=unowned.pk).exists())
        self.local.refresh_from_db()
        self.assertTrue(self.local.is_active)

    def test_add_on_other_tenant_does_not_default_to_gos(self):
        response = self.client.post(
            reverse("portal_agreements"),
            self.agreement_data("add_agreement"),
            HTTP_HOST="other.localhost",
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            MentorAgreement.objects.filter(
                organization=self.other_org, slug="shared", version=6
            ).exists()
        )
        self.local.refresh_from_db()
        self.assertTrue(self.local.is_active)

    def test_content_update_uses_local_version_and_preserves_foreign_active(self):
        response = self.client.post(
            reverse("portal_agreements"),
            self.agreement_data("update_agreement", self.local),
        )
        self.assertEqual(response.status_code, 302)
        version = MentorAgreement.objects.get(
            organization=self.org, slug="shared", version=2
        )
        self.assertEqual(version.content, "Updated content")
        self.local.refresh_from_db()
        self.foreign.refresh_from_db()
        self.assertFalse(self.local.is_active)
        self.assertTrue(self.foreign.is_active)

    def test_other_tenant_update_creates_version_in_other_tenant(self):
        response = self.client.post(
            reverse("portal_agreements"),
            self.agreement_data("update_agreement", self.foreign),
            HTTP_HOST="other.localhost",
        )
        self.assertEqual(response.status_code, 302)
        version = MentorAgreement.objects.get(
            organization=self.other_org, slug="shared", version=6
        )
        self.assertEqual(version.content, "Updated content")
        self.local.refresh_from_db()
        self.foreign.refresh_from_db()
        self.assertTrue(self.local.is_active)
        self.assertFalse(self.foreign.is_active)

    def test_same_tenant_metadata_update_and_toggle(self):
        response = self.client.post(
            reverse("portal_agreements"),
            self.agreement_data(
                "update_agreement", self.local, content="Original content"
            ),
        )
        self.assertEqual(response.status_code, 302)
        self.local.refresh_from_db()
        self.assertEqual(self.local.title, "Updated title")
        self.assertEqual(
            MentorAgreement.objects.filter(
                organization=self.org, slug="shared"
            ).count(),
            1,
        )
        response = self.client.post(
            reverse("portal_agreements"),
            self.agreement_data("toggle_agreement", self.local),
        )
        self.assertEqual(response.status_code, 302)
        self.local.refresh_from_db()
        self.foreign.refresh_from_db()
        self.assertFalse(self.local.is_active)
        self.assertTrue(self.foreign.is_active)

    def test_foreign_update_toggle_and_delete_are_rejected(self):
        for action, changes in (
            ("update_agreement", {}),
            ("update_agreement", {"content": "Original content"}),
            ("toggle_agreement", {}),
            ("delete_agreement", {}),
        ):
            with self.subTest(action=action, changes=changes), transaction.atomic():
                response = self.client.post(
                    reverse("portal_agreements"),
                    self.agreement_data(action, self.foreign, **changes),
                )
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.url, "/programs/settings/?tab=agreements")
                self.foreign.refresh_from_db()
                self.assertEqual(self.foreign.title, "Foreign agreement")
                self.assertEqual(self.foreign.content, "Original content")
                self.assertTrue(self.foreign.is_active)
                self.assertEqual(
                    MentorAgreement.objects.filter(
                        organization=self.other_org, slug="shared"
                    ).count(),
                    1,
                )
                self.assertFalse(
                    AuditLog.objects.filter(
                        resource_type="MentorAgreement",
                        resource_id=str(self.foreign.pk),
                    ).exists()
                )

    def test_delete_only_removes_local_versions_and_acceptances_and_audits(self):
        older = self.make_agreement(self.org, "shared", 2, "Local v2", active=False)
        local_adult = Adult.objects.create(
            organization=self.org, legal_first_name="Local", last_name="Mentor"
        )
        foreign_adult = Adult.objects.create(
            organization=self.other_org, legal_first_name="Foreign", last_name="Mentor"
        )
        local_acceptance = MentorAgreementAcceptance.objects.create(
            organization=self.org, adult=local_adult, agreement=self.local
        )
        foreign_acceptance = MentorAgreementAcceptance.objects.create(
            organization=self.other_org, adult=foreign_adult, agreement=self.foreign
        )
        response = self.client.post(
            reverse("portal_agreements"),
            self.agreement_data("delete_agreement", self.local),
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            MentorAgreement.objects.filter(pk__in=[self.local.pk, older.pk]).exists()
        )
        self.assertFalse(
            MentorAgreementAcceptance.objects.filter(pk=local_acceptance.pk).exists()
        )
        self.assertTrue(MentorAgreement.objects.filter(pk=self.foreign.pk).exists())
        self.assertTrue(
            MentorAgreementAcceptance.objects.filter(pk=foreign_acceptance.pk).exists()
        )
        audit = AuditLog.objects.get(
            resource_type="MentorAgreement", resource_id=str(self.local.pk)
        )
        self.assertEqual(audit.before["version_count"], 2)
        self.assertEqual(audit.before["acceptance_count"], 1)

    def test_delete_rejects_mismatched_related_records(self):
        local_adult = Adult.objects.create(
            organization=self.org, legal_first_name="Local", last_name="Mentor"
        )
        foreign_adult = Adult.objects.create(
            organization=self.other_org, legal_first_name="Foreign", last_name="Mentor"
        )
        for model in (MentorAgreementAcceptance, MentorAgreementSubmission):
            for organization, adult in (
                (self.other_org, local_adult),
                (self.org, foreign_adult),
            ):
                with (
                    self.subTest(model=model.__name__, organization=organization),
                    transaction.atomic(),
                ):
                    deletions = AuditLog.objects.filter(
                        resource_type="MentorAgreement",
                        resource_id=str(self.local.pk),
                    )
                    previous_deletions = deletions.count()
                    record = model.objects.create(
                        organization=organization, adult=adult, agreement=self.local
                    )
                    response = self.client.post(
                        reverse("portal_agreements"),
                        self.agreement_data("delete_agreement", self.local),
                    )
                    self.assertEqual(response.status_code, 302)
                    self.assertTrue(
                        MentorAgreement.objects.filter(pk=self.local.pk).exists()
                    )
                    self.assertTrue(model.objects.filter(pk=record.pk).exists())
                    self.assertEqual(deletions.count(), previous_deletions)
                    transaction.set_rollback(True)

    def test_non_lead_cannot_read_or_mutate_agreements(self):
        mentor = get_user_model().objects.create_user(username="tenant-mentor")
        UserOrganizationMembership.objects.create(
            user=mentor, organization=self.org, role="Mentor"
        )
        self.client.force_login(mentor)
        response = self.client.get(reverse("portal_settings"), {"tab": "agreements"})
        self.assertRedirects(response, reverse("home"), fetch_redirect_response=False)
        for action in (
            "add_agreement",
            "update_agreement",
            "toggle_agreement",
            "delete_agreement",
        ):
            with self.subTest(action=action):
                response = self.client.post(
                    reverse("portal_agreements"),
                    self.agreement_data(action, self.local),
                )
                self.assertRedirects(
                    response, reverse("home"), fetch_redirect_response=False
                )
                self.local.refresh_from_db()
                self.assertEqual(self.local.title, "Local agreement")
                self.assertTrue(self.local.is_active)
                self.assertEqual(
                    MentorAgreement.objects.filter(
                        organization=self.org, slug="shared"
                    ).count(),
                    1,
                )

    def test_lead_role_in_another_tenant_does_not_grant_access(self):
        UserOrganizationMembership.objects.filter(
            user=self.lead, organization=self.other_org, role="LeadMentor"
        ).update(role="Mentor")
        response = self.client.get(
            reverse("portal_settings"), HTTP_HOST="other.localhost"
        )
        self.assertRedirects(response, reverse("home"), fetch_redirect_response=False)
        response = self.client.post(
            reverse("portal_agreements"),
            self.agreement_data("toggle_agreement", self.foreign),
            HTTP_HOST="other.localhost",
        )
        self.assertRedirects(response, reverse("home"), fetch_redirect_response=False)
        self.foreign.refresh_from_db()
        self.assertTrue(self.foreign.is_active)

    def test_missing_organization_settings_fails_closed_even_for_superuser(self):
        superuser = get_user_model().objects.create_superuser(username="admin")
        factory = RequestFactory()
        request = factory.get(reverse("portal_settings"), {"tab": "agreements"})
        request.user = superuser
        request.organization = None
        with self.assertRaises(Http404):
            PortalSettingsView.as_view()(request)
        del request.organization
        with self.assertRaises(Http404):
            PortalSettingsView.as_view()(request)

    def test_missing_organization_mutations_fail_closed_even_for_superuser(self):
        superuser = get_user_model().objects.create_superuser(username="admin")
        factory = RequestFactory()
        for action in (
            "add_agreement",
            "update_agreement",
            "toggle_agreement",
            "delete_agreement",
        ):
            with self.subTest(action=action), transaction.atomic():
                request = factory.post(
                    reverse("portal_agreements"),
                    self.agreement_data(action, self.local),
                )
                request.user = superuser
                request.organization = None
                request.session = {}
                request._messages = FallbackStorage(request)
                with self.assertRaises(Http404):
                    PortalAgreementView.as_view()(request)
                del request.organization
                with self.assertRaises(Http404):
                    PortalAgreementView.as_view()(request)
                self.local.refresh_from_db()
                self.assertTrue(self.local.is_active)
