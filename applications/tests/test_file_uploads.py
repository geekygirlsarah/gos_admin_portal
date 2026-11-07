"""Tests for handling long filenames and SuspiciousFileOperation."""

from __future__ import annotations

import datetime
import shutil
import tempfile

from django.core.exceptions import SuspiciousFileOperation
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from applications.models import Application
from applications.tests.access_helpers import grant_applicant_document_access
from programs.models import Program, ProgramDocument

_TMP_MEDIA = tempfile.mkdtemp(prefix="gos-filetests-media-")


class _FailingStorage(FileSystemStorage):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.save_calls = 0

    def save(self, name, content, max_length=None):
        self.save_calls += 1
        raise SuspiciousFileOperation("Storage error")


@override_settings(
    MEDIA_ROOT=_TMP_MEDIA,
)
class FileUploadTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_TMP_MEDIA, ignore_errors=True)

    def setUp(self):
        today = timezone.localdate()
        self.program = Program.objects.create(
            name="Summer 2030",
            start_date=today + datetime.timedelta(days=60),
            end_date=today + datetime.timedelta(days=120),
            active=True,
        )
        self.doc = ProgramDocument.objects.create(
            program=self.program,
            name="Photo release form",
            file=SimpleUploadedFile(
                "form.pdf", b"%PDF-1.4 blank", content_type="application/pdf"
            ),
            is_required=True,
        )
        self.app = Application.objects.create(
            applicant_type=Application.Type.PARENT,
            email="parent@example.com",
            program=self.program,
            current_step=10,
            email_verified_at=timezone.now(),
            status=Application.Status.APPROVED,
            submitted_at=timezone.now(),
        )
        grant_applicant_document_access(self.client, self.app)

    def test_upload_very_long_filename_is_sanitized_and_saved(self):
        from applications.models import ApplicationDocumentSubmission

        field = ApplicationDocumentSubmission._meta.get_field("file")
        self.assertEqual(field.max_length, 255)

        # A very long filename that will exceed 100 chars when combined with path
        long_name = "a" * 250 + ".pdf"
        url = reverse("apply_step10", kwargs={"app_id": self.app.application_id})

        upload = SimpleUploadedFile(
            name=long_name, content=b"signed-bytes", content_type="application/pdf"
        )

        response = self.client.post(
            url,
            {
                "document_id": self.doc.pk,
                "file": upload,
            },
        )

        self.assertEqual(response.status_code, 302)  # Should be a redirect on success

        submission = ApplicationDocumentSubmission.objects.get(application=self.app)
        # Storage uses an opaque path rather than preserving the uploaded name.
        self.assertLessEqual(len(submission.file.name), 255)
        self.assertTrue(
            submission.file.name.startswith(
                f"private/application_documents/{self.app.organization_id}/"
            )
        )
        self.assertNotIn("a" * 40, submission.file.name)

    def test_sanitize_utility_directly(self):
        from programs.utils.files import sanitize_upload_filename

        # Normal name
        self.assertEqual(sanitize_upload_filename("test.pdf"), "test.pdf")

        # Long name
        long_name = "a" * 200 + ".pdf"
        sanitized = sanitize_upload_filename(long_name, max_length=50)
        self.assertEqual(len(sanitized), 50)
        self.assertTrue(sanitized.endswith(".pdf"))
        self.assertIn("_", sanitized)

        # Weird characters
        self.assertEqual(sanitize_upload_filename("test space!.pdf"), "test_space.pdf")

    def test_catch_suspicious_file_operation(self):
        from applications.models import ApplicationDocumentSubmission

        url = reverse("apply_step10", kwargs={"app_id": self.app.application_id})
        upload = SimpleUploadedFile(
            name="test.pdf", content=b"content", content_type="application/pdf"
        )

        # Ensure the submission already exists so get_or_create doesn't call save()
        # with an empty file (which wouldn't trigger the error anyway, but the mock would)
        ApplicationDocumentSubmission.objects.create(
            organization=self.app.organization,
            application=self.app,
            document=self.doc,
            file=SimpleUploadedFile(
                "existing.pdf", b"content", content_type="application/pdf"
            ),
        )

        file_storage = ApplicationDocumentSubmission._meta.get_field("file").storage
        storage_field = ApplicationDocumentSubmission._meta.get_field("file")
        original_storage = storage_field.storage
        failing_storage = _FailingStorage(location=_TMP_MEDIA)
        storage_field.storage = failing_storage
        try:
            response = self.client.post(
                url,
                {
                    "document_id": self.doc.pk,
                    "file": upload,
                },
                follow=True,
            )
        finally:
            storage_field.storage = original_storage

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, "The filename of your uploaded document is too long"
        )
        self.assertEqual(failing_storage.save_calls, 1)
