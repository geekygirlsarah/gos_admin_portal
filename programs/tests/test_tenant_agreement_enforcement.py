from tempfile import TemporaryDirectory

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import Http404
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from organizations.models import Organization, UserOrganizationMembership
from programs.models import (
    Adult,
    MentorAgreement,
    MentorAgreementAcceptance,
    MentorAgreementSubmission,
)
from programs.services.digest import collect_mentor_digest_data
from programs.views.mentor_agreement import mentor_agreement_view


@override_settings(
    ALLOWED_HOSTS=[".momentumteam.app", "testserver"],
    MOMENTUM_ROOT_DOMAIN="momentumteam.app",
    MENTOR_AGREEMENT_ENABLED=True,
)
class TenantAgreementEnforcementTests(TestCase):
    def setUp(self):
        self.alpha = Organization.objects.create(
            name="Alpha", slug="agreement-alpha", subdomain="agreement-alpha"
        )
        self.beta = Organization.objects.create(
            name="Beta", slug="agreement-beta", subdomain="agreement-beta"
        )
        self.user = User.objects.create_user(username="agreement-mentor")
        self.adult = Adult.objects.create(
            organization=self.alpha,
            user=self.user,
            legal_first_name="A",
            last_name="Mentor",
            is_mentor=True,
            login_enabled=True,
        )
        for organization in (self.alpha, self.beta):
            UserOrganizationMembership.objects.update_or_create(
                organization=organization,
                user=self.user,
                role="Mentor",
                defaults={"is_active": True},
            )
        self.alpha_agreement = MentorAgreement.objects.create(
            organization=self.alpha,
            slug="policy",
            version=1,
            title="Alpha agreement",
            content="Alpha terms",
            effective_date="2025-01-01",
        )
        self.beta_agreement = MentorAgreement.objects.create(
            organization=self.beta,
            slug="policy",
            version=1,
            title="Beta agreement",
            content="Beta terms",
            effective_date="2025-01-01",
        )
        self.alpha_host = "agreement-alpha.momentumteam.app"
        self.beta_host = "agreement-beta.momentumteam.app"
        self.client.force_login(self.user)

    def test_agreement_page_shows_only_current_organization(self):
        response = self.client.get(
            reverse("mentor_agreement"), HTTP_HOST=self.alpha_host
        )
        self.assertContains(response, "Alpha terms")
        self.assertNotContains(response, "Beta terms")

    def test_anonymous_agreement_page_remains_public_and_scoped(self):
        self.client.logout()
        response = self.client.get(
            reverse("mentor_agreement"), HTTP_HOST=self.alpha_host
        )
        self.assertContains(response, "Alpha terms")
        self.assertNotContains(response, "Beta terms")

    def test_signed_in_nonmember_cannot_view_or_accept(self):
        UserOrganizationMembership.objects.filter(
            user=self.user, organization=self.alpha
        ).update(is_active=False)
        for method in (self.client.get, self.client.post):
            with self.subTest(method=method.__name__):
                response = method(
                    reverse("mentor_agreement"), HTTP_HOST=self.alpha_host
                )
                self.assertEqual(response.status_code, 403)

    def test_agree_records_only_current_organization(self):
        response = self.client.post(
            reverse("mentor_agreement"), {"action": "agree"}, HTTP_HOST=self.alpha_host
        )
        self.assertEqual(response.status_code, 302)
        acceptance = MentorAgreementAcceptance.objects.get(adult=self.adult)
        self.assertEqual(acceptance.agreement, self.alpha_agreement)
        self.assertEqual(acceptance.organization, self.alpha)

    def test_upload_and_acceptance_require_matching_adult_organization(self):
        for action in ("agree", "upload"):
            with self.subTest(action=action):
                response = self.client.post(
                    reverse("mentor_agreement"),
                    {"action": action, "upload_agreement_id": self.beta_agreement.pk},
                    HTTP_HOST=self.beta_host,
                )
                self.assertEqual(response.status_code, 403)
        response = self.client.get(
            reverse("mentor_agreement"), HTTP_HOST=self.beta_host
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            MentorAgreementAcceptance.objects.filter(adult=self.adult).exists()
        )
        self.assertFalse(
            MentorAgreementSubmission.objects.filter(adult=self.adult).exists()
        )

    def test_foreign_agreement_upload_is_rejected(self):
        response = self.client.post(
            reverse("mentor_agreement"),
            {"action": "upload", "upload_agreement_id": self.beta_agreement.pk},
            HTTP_HOST=self.alpha_host,
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            MentorAgreementSubmission.objects.filter(adult=self.adult).exists()
        )

    def test_submission_is_owned_by_current_organization(self):
        self.alpha_agreement.document = "agreement.pdf"
        self.alpha_agreement.save()
        with (
            TemporaryDirectory() as media_root,
            override_settings(MEDIA_ROOT=media_root),
        ):
            response = self.client.post(
                reverse("mentor_agreement"),
                {
                    "action": "upload",
                    "upload_agreement_id": self.alpha_agreement.pk,
                    f"file_{self.alpha_agreement.pk}": SimpleUploadedFile(
                        "signed.pdf",
                        b"test signed document",
                        content_type="application/pdf",
                    ),
                },
                HTTP_HOST=self.alpha_host,
            )
            self.assertEqual(response.status_code, 302)
            submission = MentorAgreementSubmission.objects.get(adult=self.adult)
            self.assertEqual(submission.agreement, self.alpha_agreement)
            self.assertEqual(submission.organization, self.alpha)

    def test_agreement_page_without_tenant_fails_closed(self):
        request = RequestFactory().get(reverse("mentor_agreement"))
        request.user = self.user
        request.organization = None
        with self.assertRaises(Http404):
            mentor_agreement_view(request)

    def test_enforcement_ignores_other_organizations_agreements(self):
        MentorAgreementAcceptance.objects.create(
            organization=self.alpha, adult=self.adult, agreement=self.alpha_agreement
        )
        self.assertTrue(self.adult.has_accepted_current_agreement())
        self.assertTrue(
            MentorAgreementAcceptance.has_accepted_for_user(
                self.user, organization=self.alpha
            )
        )
        response = self.client.get(
            reverse("profile_dashboard"), HTTP_HOST=self.alpha_host
        )
        self.assertEqual(response.status_code, 200)

    def test_foreign_profile_is_not_accepted_for_current_tenant(self):
        self.beta_agreement.is_active = False
        self.beta_agreement.save()
        self.assertFalse(
            MentorAgreementAcceptance.has_accepted_for_user(
                self.user, organization=self.beta
            )
        )

    def test_foreign_owned_acceptance_does_not_count(self):
        MentorAgreementAcceptance.objects.create(
            organization=self.beta, adult=self.adult, agreement=self.alpha_agreement
        )
        self.assertFalse(self.adult.has_accepted_current_agreement())
        self.assertFalse(
            MentorAgreementAcceptance.has_accepted_for_user(
                self.user, organization=self.alpha
            )
        )
        response = self.client.get(
            reverse("mentor_agreement"), HTTP_HOST=self.alpha_host
        )
        self.assertContains(response, "Alpha terms")
        data = collect_mentor_digest_data(self.adult)
        self.assertEqual(
            data["action_items"]["pending_agreements"], [self.alpha_agreement]
        )
        response = self.client.post(
            reverse("mentor_agreement"), {"action": "agree"}, HTTP_HOST=self.alpha_host
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            MentorAgreementAcceptance.objects.filter(adult=self.adult).count(), 1
        )

    def test_digest_lists_only_current_organization_pending_agreements(self):
        data = collect_mentor_digest_data(self.adult)
        self.assertEqual(
            data["action_items"]["pending_agreements"], [self.alpha_agreement]
        )
        MentorAgreementAcceptance.objects.create(
            organization=self.alpha, adult=self.adult, agreement=self.alpha_agreement
        )
        data = collect_mentor_digest_data(self.adult)
        self.assertEqual(data["action_items"]["pending_agreements"], [])
