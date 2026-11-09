"""Tests for the guest form management views (create/edit/list)."""

from django.contrib.auth.models import Group, Permission, User
from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from audit.events import AuditEvent
from audit.models import AuditLog
from guest_forms.models import GuestForm, GuestFormSubmission
from guest_forms.views.review import GuestFormReviewListView
from organizations.models import Organization, UserOrganizationMembership


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    }
)
class GuestFormManageViewTests(TestCase):
    """Regression: the manage form must render the URL fields so saving works."""

    @classmethod
    def setUpTestData(cls):
        cls.org, _ = Organization.objects.get_or_create(
            slug="gos", defaults={"name": "Girls of Steel"}
        )
        cls.beta = Organization.objects.create(
            name="Guest Review Beta",
            slug="guest-review-beta",
            subdomain="guest-review-beta",
        )
        cls.user = User.objects.create_user(
            username="leadmentor", password="pass12345"  # nosec B106
        )
        group, _ = Group.objects.get_or_create(name="LeadMentor")
        cls.user.groups.add(group)
        UserOrganizationMembership.objects.create(
            user=cls.user,
            organization=cls.org,
            role="LeadMentor",
            is_active=True,
        )

    def setUp(self):
        self.client.login(username="leadmentor", password="pass12345")  # nosec B106

    def _pdf(self):
        return SimpleUploadedFile(
            "form.pdf", b"%PDF-1.4 fake pdf", content_type="application/pdf"
        )

    def _submission(self, guest_form, organization, first_name="Test"):
        return GuestFormSubmission.objects.create(
            organization=organization,
            guest_form=guest_form,
            participant_first_name=first_name,
            participant_last_name="Guest",
            file=SimpleUploadedFile(f"{first_name.lower()}.pdf", b"%PDF-1.4 test"),
        )

    def _valid_post_data(self, **overrides):
        data = {
            "name": "Photo Release",
            "description": "",
            "is_required": "on",
            "display_order": "0",
            "is_active": "on",
            "legal_notices_url": "https://www.cmu.edu/legal/",
            "safety_guidelines_url": "",
        }
        data.update(overrides)
        return data

    def test_create_page_renders_url_fields(self):
        resp = self.client.get(reverse("guest_form_create"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'name="legal_notices_url"')
        self.assertContains(resp, 'name="safety_guidelines_url"')

    def test_membership_only_lead_can_manage_guest_forms(self):
        lead = User.objects.create_user(
            username="membership-only-lead", password="pass12345"  # nosec B106
        )
        UserOrganizationMembership.objects.create(
            user=lead,
            organization=self.org,
            role="LeadMentor",
            is_active=True,
        )
        self.client.force_login(lead)

        response = self.client.get(reverse("guest_form_manage_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("guest_form_manage_list"))

    def test_global_reviewer_permission_does_not_override_tenant_role(self):
        permission = Permission.objects.get(
            content_type=ContentType.objects.get_for_model(GuestForm),
            codename="review_guestform",
        )
        self.user.user_permissions.add(permission)
        UserOrganizationMembership.objects.create(
            user=self.user,
            organization=self.beta,
            role="Student",
            is_active=True,
        )

        response = self.client.get(
            reverse("guest_form_review_list"),
            HTTP_HOST="guest-review-beta.localhost",
        )

        self.assertEqual(response.status_code, 403)

    def test_superuser_can_review_current_organization(self):
        admin = User.objects.create_superuser(
            username="platform-guest-review-admin", email="admin@example.test"
        )
        self.client.force_login(admin)

        response = self.client.get(
            reverse("guest_form_review_list"),
            HTTP_HOST="guest-review-beta.localhost",
        )

        self.assertEqual(response.status_code, 200)

    def test_global_reviewer_permission_without_organization_fails_closed(self):
        permission = Permission.objects.get(
            content_type=ContentType.objects.get_for_model(GuestForm),
            codename="review_guestform",
        )
        self.user.user_permissions.add(permission)

        request = RequestFactory().get(reverse("guest_form_review_list"))
        request.user = self.user
        view = GuestFormReviewListView()
        view.request = request

        self.assertFalse(view.has_permission())

    def test_manage_form_has_no_form_type(self):
        """Regression: form_type moved from GuestForm to the submission itself."""
        resp = self.client.get(reverse("guest_form_create"))
        self.assertNotContains(resp, 'name="form_type"')

    def test_edit_page_renders_url_fields(self):
        guest_form = GuestForm.objects.create(
            name="Adult Waiver", file=self._pdf(), organization=self.org
        )
        resp = self.client.get(reverse("guest_form_edit", args=[guest_form.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'name="legal_notices_url"')
        self.assertContains(resp, 'name="safety_guidelines_url"')

    def test_create_post_saves_guest_form(self):
        data = self._valid_post_data()
        data["file"] = self._pdf()
        resp = self.client.post(reverse("guest_form_create"), data)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("guest_form_manage_list"))
        self.assertTrue(GuestForm.objects.filter(name="Photo Release").exists())
        self.assertTrue(
            GuestForm.objects.filter(
                name="Photo Release", organization=self.org
            ).exists()
        )

    def test_update_post_saves_changes(self):
        guest_form = GuestForm.objects.create(
            name="Old Name", file=self._pdf(), organization=self.org
        )
        data = self._valid_post_data(name="New Name")
        data["file"] = self._pdf()
        resp = self.client.post(reverse("guest_form_edit", args=[guest_form.pk]), data)
        self.assertEqual(resp.status_code, 302)
        guest_form.refresh_from_db()
        self.assertEqual(guest_form.name, "New Name")

    def test_manage_list_counts_only_local_submissions(self):
        guest_form = GuestForm.objects.create(
            name="Alpha Count Form",
            file=self._pdf(),
            organization=self.org,
        )
        self._submission(guest_form, self.org, first_name="Local")
        self._submission(guest_form, self.beta, first_name="Foreign")

        response = self.client.get(reverse("guest_form_manage_list"))

        self.assertEqual(response.status_code, 200)
        form = response.context["forms"].get(pk=guest_form.pk)
        self.assertEqual(form.tenant_submission_count, 1)

    def test_review_queue_and_detail_hide_foreign_linked_form(self):
        guest_form = GuestForm.objects.create(
            name="Alpha Private Guest Form",
            file=self._pdf(),
            organization=self.org,
        )
        submission = self._submission(guest_form, self.beta, first_name="Beta")
        beta_lead = User.objects.create_user(
            username="beta-guest-review-lead", password="pass12345"  # nosec B106
        )
        UserOrganizationMembership.objects.create(
            user=beta_lead,
            organization=self.beta,
            role="LeadMentor",
            is_active=True,
        )
        self.client.force_login(beta_lead)

        list_response = self.client.get(
            reverse("guest_form_review_list"),
            HTTP_HOST="guest-review-beta.localhost",
        )
        detail_response = self.client.get(
            reverse("guest_form_review_detail", args=[submission.pk]),
            HTTP_HOST="guest-review-beta.localhost",
        )

        self.assertEqual(list_response.status_code, 200)
        self.assertNotContains(list_response, "Alpha Private Guest Form")
        self.assertNotEqual(detail_response.status_code, 200)
        self.assertNotIn(b"Alpha Private Guest Form", detail_response.content)

    def test_delete_confirmation_counts_local_submissions_and_warns_of_mismatch(self):
        guest_form = GuestForm.objects.create(
            name="Alpha Delete Form",
            file=self._pdf(),
            organization=self.org,
        )
        self._submission(guest_form, self.org, first_name="Local")
        self._submission(guest_form, self.beta, first_name="Foreign")

        response = self.client.get(reverse("guest_form_delete", args=[guest_form.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["submission_count"], 1)
        self.assertTrue(response.context["deletion_blocked"])

    def test_delete_blocks_foreign_submissions_before_audit_or_cascade(self):
        guest_form = GuestForm.objects.create(
            name="Alpha Protected Form",
            file=self._pdf(),
            organization=self.org,
        )
        foreign_submission = self._submission(
            guest_form, self.beta, first_name="Foreign"
        )
        audit_count = AuditLog.objects.filter(
            actor=self.user, event=AuditEvent.RECORD_DELETED
        ).count()

        response = self.client.post(reverse("guest_form_delete", args=[guest_form.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertTrue(GuestForm.objects.filter(pk=guest_form.pk).exists())
        self.assertTrue(
            GuestFormSubmission.objects.filter(pk=foreign_submission.pk).exists()
        )
        self.assertEqual(
            AuditLog.objects.filter(
                actor=self.user, event=AuditEvent.RECORD_DELETED
            ).count(),
            audit_count,
        )

    def test_delete_with_only_local_submissions_keeps_normal_audit_behavior(self):
        guest_form = GuestForm.objects.create(
            name="Alpha Local Delete Form",
            file=self._pdf(),
            organization=self.org,
        )
        submission = self._submission(guest_form, self.org, first_name="Local")
        audit_count = AuditLog.objects.filter(
            actor=self.user, event=AuditEvent.RECORD_DELETED
        ).count()

        response = self.client.post(reverse("guest_form_delete", args=[guest_form.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertFalse(GuestForm.objects.filter(pk=guest_form.pk).exists())
        self.assertFalse(GuestFormSubmission.objects.filter(pk=submission.pk).exists())
        self.assertEqual(
            AuditLog.objects.filter(
                actor=self.user, event=AuditEvent.RECORD_DELETED
            ).count(),
            audit_count + 1,
        )
