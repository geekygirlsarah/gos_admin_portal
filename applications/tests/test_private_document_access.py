from __future__ import annotations

import secrets
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from applications.models import Application, ApplicationDocumentSubmission
from organizations.models import Organization, UserOrganizationMembership
from programs.models import Program, ProgramDocument

_TMP_MEDIA = tempfile.mkdtemp(prefix="application-private-documents-")


@override_settings(
    ALLOWED_HOSTS=[".localhost", "testserver"],
    MAILERS={"default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}},
    MEDIA_ROOT=_TMP_MEDIA,
)
class PrivateApplicationDocumentAccessTests(TestCase):
    def setUp(self):
        tenant_key = secrets.token_hex(4)
        self.organization = Organization.objects.create(
            name="Private Docs Alpha",
            slug=f"private-docs-alpha-{tenant_key}",
            subdomain=f"private-docs-alpha-{tenant_key}",
        )
        self.foreign_organization = Organization.objects.create(
            name="Private Docs Beta",
            slug=f"private-docs-beta-{tenant_key}",
            subdomain=f"private-docs-beta-{tenant_key}",
        )
        self.program = Program.objects.create(
            organization=self.organization,
            name="Private Docs Program",
        )
        self.document = ProgramDocument.objects.create(
            program=self.program,
            name="Release form",
            file=SimpleUploadedFile("blank.pdf", b"blank form"),
            is_required=True,
        )
        self.application = Application.objects.create(
            organization=self.organization,
            applicant_type=Application.Type.PARENT,
            email="parent@example.test",
            email_verified_at=timezone.now(),
            program=self.program,
            current_step=10,
            status=Application.Status.APPROVED,
        )
        self.file_content = b"confidential signed application bytes"
        self.submission = ApplicationDocumentSubmission.objects.create(
            organization=self.organization,
            application=self.application,
            document=self.document,
            file=SimpleUploadedFile("signed-release.pdf", self.file_content),
        )
        self.host = f"{self.organization.subdomain}.localhost"

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_TMP_MEDIA, ignore_errors=True)

    def test_signed_document_direct_media_url_is_not_public(self):
        legacy_path = Path(_TMP_MEDIA, "application_documents", "legacy", "signed.pdf")
        legacy_path.parent.mkdir(parents=True, exist_ok=True)
        legacy_path.write_bytes(self.file_content)
        for path in (
            self.submission.file.url,
            "/media/application_documents/legacy/signed.pdf",
        ):
            for debug in (True, False):
                with (
                    self.subTest(path=path, debug=debug),
                    override_settings(DEBUG=debug),
                ):
                    with self.assertNumQueries(0):
                        response = self.client.get(path, HTTP_HOST=self.host)
                    self.assertNotEqual(response.status_code, 200)
                    if response.streaming:
                        self.assertNotIn(
                            self.file_content, b"".join(response.streaming_content)
                        )
                    else:
                        self.assertNotIn(self.file_content, response.content)

    def test_private_application_files_are_stored_in_org_scoped_random_path(self):
        path = self.submission.file.name
        self.assertTrue(
            path.startswith(f"private/application_documents/{self.organization.pk}/")
        )
        self.assertNotIn("signed-release", path)

    def test_applicant_cannot_download_by_application_id_alone(self):
        response = self.client.get(
            reverse(
                "apply_step10_document_download",
                kwargs={
                    "app_id": self.application.application_id,
                    "document_id": self.document.pk,
                },
            ),
            HTTP_HOST=self.host,
        )
        self.assertNotEqual(response.status_code, 200)
        if response.streaming:
            self.assertNotIn(self.file_content, b"".join(response.streaming_content))
        else:
            self.assertNotIn(self.file_content, response.content)

    def test_applicant_can_download_after_fresh_code_verification_in_same_session(self):
        access_url = reverse(
            "apply_step10_document_access",
            kwargs={"app_id": self.application.application_id},
        )
        codes = []
        with patch(
            "applications.views.utils.send_otp_email",
            side_effect=lambda application, code, request=None: codes.append(code),
        ):
            response = self.client.post(
                access_url,
                {"action": "send"},
                HTTP_HOST=self.host,
            )
        self.assertIn(response.status_code, (200, 302))
        self.assertEqual(len(codes), 1)

        response = self.client.post(
            access_url,
            {"action": "verify", "code": codes[0]},
            HTTP_HOST=self.host,
        )
        self.assertEqual(response.status_code, 302)

        download = self.client.get(
            reverse(
                "apply_step10_document_download",
                kwargs={
                    "app_id": self.application.application_id,
                    "document_id": self.document.pk,
                },
            ),
            HTTP_HOST=self.host,
        )
        self.assertEqual(download.status_code, 200)
        self.assertEqual(b"".join(download.streaming_content), self.file_content)

        session = self.client.session
        access_key = (
            f"application_document_access:{self.organization.pk}:"
            f"{self.application.application_id}"
        )
        access = session[access_key]
        access["expires_at"] = 0
        session[access_key] = access
        session.save()
        expired = self.client.get(
            reverse(
                "apply_step10_document_download",
                kwargs={
                    "app_id": self.application.application_id,
                    "document_id": self.document.pk,
                },
            ),
            HTTP_HOST=self.host,
        )
        self.assertNotEqual(expired.status_code, 200)

        other_browser = self.client_class()
        code_replay = other_browser.post(
            access_url,
            {"action": "verify", "code": codes[0]},
            HTTP_HOST=self.host,
        )
        self.assertNotEqual(code_replay.status_code, 302)
        denied = other_browser.get(
            reverse(
                "apply_step10_document_download",
                kwargs={
                    "app_id": self.application.application_id,
                    "document_id": self.document.pk,
                },
            ),
            HTTP_HOST=self.host,
        )
        self.assertNotEqual(denied.status_code, 200)
        if denied.streaming:
            self.assertNotIn(self.file_content, b"".join(denied.streaming_content))
        else:
            self.assertNotIn(self.file_content, denied.content)

    def test_existing_legacy_submission_remains_available_through_authorized_endpoint(
        self,
    ):
        legacy_path = Path(
            _TMP_MEDIA,
            "application_documents",
            "legacy",
            "existing-signed.pdf",
        )
        legacy_path.parent.mkdir(parents=True, exist_ok=True)
        legacy_path.write_bytes(self.file_content)
        self.submission.file = "application_documents/legacy/existing-signed.pdf"
        self.submission.save(update_fields=["file"])

        from applications.tests.access_helpers import grant_applicant_document_access

        grant_applicant_document_access(self.client, self.application)
        response = self.client.get(
            reverse(
                "apply_step10_document_download",
                kwargs={
                    "app_id": self.application.application_id,
                    "document_id": self.document.pk,
                },
            ),
            HTTP_HOST=self.host,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), self.file_content)

    @override_settings(APPLY_RATE_LIMIT_ENABLED=True, APPLY_OTP_SEND_LIMIT=0)
    def test_document_access_code_request_obeys_otp_rate_limit(self):
        response = self.client.post(
            reverse(
                "apply_step10_document_access",
                kwargs={"app_id": self.application.application_id},
            ),
            {"action": "send"},
            HTTP_HOST=self.host,
        )
        self.assertEqual(response.status_code, 429)

    def test_staff_download_requires_tenant_reviewer_access_and_uses_protected_route(
        self,
    ):
        user = get_user_model().objects.create_superuser(
            username="private-doc-reviewer",
            email="reviewer@example.test",
            password=secrets.token_urlsafe(32),
        )
        UserOrganizationMembership.objects.create(
            user=user,
            organization=self.organization,
            role="LeadMentor",
            is_active=True,
        )
        self.client.force_login(user)
        detail = self.client.get(
            reverse(
                "application_review_detail",
                kwargs={"app_id": self.application.application_id},
            ),
            HTTP_HOST=self.host,
        )
        self.assertContains(
            detail,
            reverse(
                "application_review_document_download",
                kwargs={
                    "app_id": self.application.application_id,
                    "submission_id": self.submission.pk,
                },
            ),
        )
        self.assertNotContains(detail, self.submission.file.url)

        download = self.client.get(
            reverse(
                "application_review_document_download",
                kwargs={
                    "app_id": self.application.application_id,
                    "submission_id": self.submission.pk,
                },
            ),
            HTTP_HOST=self.host,
        )
        self.assertEqual(download.status_code, 200)
        self.assertEqual(b"".join(download.streaming_content), self.file_content)

        denied = self.client.get(
            reverse(
                "application_review_document_download",
                kwargs={
                    "app_id": self.application.application_id,
                    "submission_id": self.submission.pk,
                },
            ),
            HTTP_HOST=f"{self.foreign_organization.subdomain}.localhost",
        )
        self.assertNotEqual(denied.status_code, 200)

    def test_approved_application_email_cannot_be_changed_from_early_wizard_step(self):
        response = self.client.post(
            reverse("apply_step2", kwargs={"app_id": self.application.application_id}),
            {
                "applicant_type": Application.Type.PARENT,
                "email": "attacker@example.test",
            },
            HTTP_HOST=self.host,
        )
        self.application.refresh_from_db()
        self.assertEqual(self.application.email, "parent@example.test")
        self.assertEqual(self.application.status, Application.Status.APPROVED)
        self.assertNotEqual(response.status_code, 500)

    def test_approved_application_cannot_be_downgraded_from_step_nine(self):
        response = self.client.post(
            reverse("apply_step9", kwargs={"app_id": self.application.application_id}),
            {"confirm": "on"},
            HTTP_HOST=self.host,
        )
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, Application.Status.APPROVED)
        self.assertNotEqual(response.status_code, 500)
