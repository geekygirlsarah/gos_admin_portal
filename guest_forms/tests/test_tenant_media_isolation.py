from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.signals import request_finished
from django.db.models.signals import post_init
from django.http import Http404
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from audit.events import AuditEvent
from audit.models import AuditLog
from GoSAdminPortal.test_runner import (
    _assign_default_test_org,
    install_default_test_org_hook,
)
from guest_forms.models import GuestForm, GuestFormSubmission
from guest_forms.views.public import GuestFormIndexView
from guest_forms.views.review import GuestFormSubmissionDownloadView
from organizations.models import Organization, UserOrganizationMembership


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    }
)
class GuestFormTenantMediaIsolationTests(TestCase):
    tenant_models = (GuestForm, GuestFormSubmission)

    def setUp(self):
        self.addCleanup(install_default_test_org_hook)
        for model in self.tenant_models:
            post_init.disconnect(
                receiver=_assign_default_test_org,
                sender=model,
                dispatch_uid=f"default-test-org-{model._meta.label}",
            )

        self.alpha = Organization.objects.get(slug="gos")
        self.beta = Organization.objects.create(
            name="Guest Files Beta",
            slug="guest-files-beta",
            subdomain="guest-files-beta",
        )
        self.lead = get_user_model().objects.create_user(
            username="guest-files-lead", password="password123"
        )  # nosec B106
        UserOrganizationMembership.objects.create(
            user=self.lead,
            organization=self.alpha,
            role="LeadMentor",
            is_active=True,
        )
        self.client.force_login(self.lead)

    def _pdf(self, name="signed.pdf"):
        return SimpleUploadedFile(name, b"%PDF-1.4 signed guest form")

    def _guest_form(self, organization=None, name="Photo Release"):
        organization = organization or self.alpha
        return GuestForm.objects.create(
            organization=organization,
            name=name,
            file=self._pdf("blank.pdf"),
        )

    def _submission(self, guest_form, organization=None):
        return GuestFormSubmission.objects.create(
            organization=organization or guest_form.organization,
            guest_form=guest_form,
            participant_first_name="Alex",
            participant_last_name="Guest",
            file=self._pdf(),
        )

    def _valid_public_post(self):
        return {
            "participant_type": "student",
            "participant_first_name": "Alex",
            "participant_last_name": "Smith",
            "email": "alex@example.test",
            "phone_number": "4125551234",
            "phone_type": "cell",
            "team_number": "3504",
            "emergency_contact_name": "Jamie Smith",
            "emergency_contact_phone": "4125550000",
            "emergency_contact_relationship": "parent_guardian",
            "emergency_contact_other": "",
            "agreed_legal_notices": "on",
            "agreed_safety_guidelines": "on",
            "file": self._pdf(),
        }

    def test_guest_form_creation_assigns_request_organization_without_test_hook(self):
        response = self.client.post(
            reverse("guest_form_create"),
            {
                "name": "Tenant-owned Form",
                "description": "",
                "is_required": "on",
                "display_order": "0",
                "is_active": "on",
                "legal_notices_url": "https://www.cmu.edu/legal/",
                "safety_guidelines_url": "",
                "file": self._pdf("form.pdf"),
            },
        )

        self.assertEqual(response.status_code, 302)
        guest_form = GuestForm.objects.get(name="Tenant-owned Form")
        self.assertEqual(guest_form.organization_id, self.alpha.pk)

    def test_public_submission_assigns_guest_form_organization_without_test_hook(self):
        guest_form = self._guest_form()

        response = self.client.post(
            reverse("guest_form_detail", args=[guest_form.slug]),
            self._valid_public_post(),
        )

        self.assertEqual(response.status_code, 302)
        submission = GuestFormSubmission.objects.get(
            guest_form=guest_form, email="alex@example.test"
        )
        self.assertEqual(submission.organization_id, self.alpha.pk)

    def test_public_guest_form_index_fails_closed_without_organization(self):
        request = RequestFactory().get(reverse("guest_form_index"))
        request.organization = None

        with self.assertRaises(Http404):
            GuestFormIndexView.as_view()(request)

    def test_lead_can_download_local_submission_with_audit(self):
        guest_form = self._guest_form()
        submission = self._submission(guest_form)
        url = reverse("guest_form_submission_download", args=[submission.pk])

        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            b"".join(response.streaming_content), b"%PDF-1.4 signed guest form"
        )
        self.assertTrue(response["Content-Disposition"].startswith("attachment;"))
        self.assertIn("private, no-store", response["Cache-Control"])
        self.assertEqual(
            AuditLog.objects.filter(
                actor=self.lead,
                event=AuditEvent.SENSITIVE_DATA_VIEW,
                resource_id=str(submission.pk),
            ).count(),
            1,
        )

    def test_superuser_can_download_local_submission(self):
        guest_form = self._guest_form()
        submission = self._submission(guest_form)
        admin = get_user_model().objects.create_superuser(
            username="guest-files-admin", email="admin@example.test"
        )
        self.client.force_login(admin)

        response = self.client.get(
            reverse("guest_form_submission_download", args=[submission.pk])
        )

        self.assertEqual(response.status_code, 200)

    def test_signed_submission_download_requires_organization_context(self):
        guest_form = self._guest_form()
        submission = self._submission(guest_form)
        admin = get_user_model().objects.create_superuser(
            username="contextless-guest-files-admin", email="admin@example.test"
        )
        request = RequestFactory().get(
            reverse("guest_form_submission_download", args=[submission.pk])
        )
        request.user = admin
        request.organization = None

        with self.assertRaises(Http404):
            GuestFormSubmissionDownloadView.as_view()(
                request, submission_id=submission.pk
            )

    def test_foreign_submission_and_lower_role_cannot_be_downloaded(self):
        foreign_form = self._guest_form(self.beta, name="Beta Release")
        foreign_submission = self._submission(foreign_form)
        url = reverse("guest_form_submission_download", args=[foreign_submission.pk])

        response = self.client.get(url)

        self.assertNotEqual(response.status_code, 200)

        mismatched_submission = self._submission(foreign_form, organization=self.alpha)
        mismatch_response = self.client.get(
            reverse("guest_form_submission_download", args=[mismatched_submission.pk])
        )
        self.assertNotEqual(mismatch_response.status_code, 200)

        parent = get_user_model().objects.create_user(username="guest-files-parent")
        permission = Permission.objects.get(
            content_type__app_label="guest_forms", codename="review_guestform"
        )
        parent.user_permissions.add(permission)
        UserOrganizationMembership.objects.create(
            user=parent,
            organization=self.alpha,
            role="Parent",
            is_active=True,
        )
        self.client.force_login(parent)

        response = self.client.get(
            reverse(
                "guest_form_submission_download",
                args=[self._submission(self._guest_form()).pk],
            )
        )

        self.assertNotEqual(response.status_code, 200)

    def test_direct_signed_submission_media_url_is_denied(self):
        with TemporaryDirectory() as media_root:
            file_path = (
                Path(media_root)
                / "guest_form_submissions"
                / "photo-release"
                / "signed.pdf"
            )
            file_path.parent.mkdir(parents=True)
            file_path.write_bytes(b"private signed guest form")
            finished_requests = []

            def record_request_finished(**kwargs):
                finished_requests.append(True)

            request_finished.connect(record_request_finished, weak=False)
            try:
                with override_settings(MEDIA_ROOT=media_root):
                    response = self.client.get(
                        "/media/guest_form_submissions/photo-release/signed.pdf"
                    )
            finally:
                request_finished.disconnect(record_request_finished)

        self.assertEqual(response.status_code, 404)
        self.assertEqual(len(finished_requests), 1)
