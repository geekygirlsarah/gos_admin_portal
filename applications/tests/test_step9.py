"""Tests for the application wizard Step 9 (post-approval signed docs)."""

from __future__ import annotations

import datetime
import shutil
import tempfile

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from applications.models import Application, ApplicationDocumentSubmission
from applications.services import send_application_approved_email
from programs.models import Program, ProgramDocument

# Use an isolated MEDIA_ROOT so file uploads from tests don't pollute the
# real media tree.
_TMP_MEDIA = tempfile.mkdtemp(prefix="gos-step9-media-")


def _approved_application(program, **overrides):
    defaults = dict(
        applicant_type=Application.Type.PARENT,
        email="parent@example.com",
        program=program,
        current_step=9,
        email_verified_at=timezone.now(),
        status=Application.Status.APPROVED,
        submitted_at=timezone.now(),
        data={
            "step5": {"legal_first_name": "Ada", "last_name": "Lovelace"},
            "step6": {
                "first_name": "Pat",
                "last_name": "Parent",
                "email": "parent@example.com",
            },
            "step7": {
                "first_name": "Sam",
                "last_name": "Spouse",
                "relationship_to_student": "guardian",
            },
        },
    )
    defaults.update(overrides)
    return Application.objects.create(**defaults)


def _make_blank_pdf(name="form.pdf"):
    return SimpleUploadedFile(
        name=name, content=b"%PDF-1.4 blank", content_type="application/pdf"
    )


def _make_signed_upload(name="signed.pdf", content=b"signed-bytes"):
    return SimpleUploadedFile(
        name=name, content=content, content_type="application/pdf"
    )


@override_settings(
    MEDIA_ROOT=_TMP_MEDIA,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
class Step9DocumentsTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_TMP_MEDIA, ignore_errors=True)

    def setUp(self):
        today = timezone.localdate()
        self.program = Program.objects.create(
            name="Summer 2030",
            year=2030,
            start_date=today + datetime.timedelta(days=60),
            end_date=today + datetime.timedelta(days=120),
            active=True,
        )
        self.doc_required = ProgramDocument.objects.create(
            program=self.program,
            name="Photo release form",
            description="Sign and return.",
            file=_make_blank_pdf("photo_release.pdf"),
            is_required=True,
            display_order=1,
        )
        self.doc_optional = ProgramDocument.objects.create(
            program=self.program,
            name="Optional handbook ack",
            file=_make_blank_pdf("handbook.pdf"),
            is_required=False,
            display_order=2,
        )
        mail.outbox = []

    # -- Access gating ------------------------------------------------------

    def test_non_approved_application_is_redirected_away(self):
        app = _approved_application(
            self.program,
            status=Application.Status.SUBMITTED,
            current_step=9,
        )
        url = reverse("apply_step9", kwargs={"app_id": app.application_id})
        response = self.client.get(url)
        # Either to submitted page or to a wizard step, but NEVER 200 on /step9/.
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("/step9/", response.url)

    def test_approved_application_can_view_documents(self):
        app = _approved_application(self.program)
        response = self.client.get(
            reverse("apply_step9", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Photo release form")
        self.assertContains(response, "Optional handbook ack")
        # Required badge shown for required doc.
        self.assertContains(response, "Required")

    # -- Upload behavior ----------------------------------------------------

    def test_upload_creates_submission(self):
        app = _approved_application(self.program)
        response = self.client.post(
            reverse("apply_step9", kwargs={"app_id": app.application_id}),
            {
                "document_id": self.doc_required.pk,
                "file": _make_signed_upload("photo_release_signed.pdf"),
            },
        )
        self.assertRedirects(
            response,
            reverse("apply_step9", kwargs={"app_id": app.application_id}),
            fetch_redirect_response=False,
        )
        subs = ApplicationDocumentSubmission.objects.filter(application=app)
        self.assertEqual(subs.count(), 1)
        sub = subs.get()
        self.assertEqual(sub.document_id, self.doc_required.pk)
        self.assertTrue(sub.file.name.endswith(".pdf"))

    def test_reupload_replaces_existing_submission(self):
        app = _approved_application(self.program)
        url = reverse("apply_step9", kwargs={"app_id": app.application_id})
        self.client.post(
            url,
            {
                "document_id": self.doc_required.pk,
                "file": _make_signed_upload("first.pdf", b"first"),
            },
        )
        self.client.post(
            url,
            {
                "document_id": self.doc_required.pk,
                "file": _make_signed_upload("second.pdf", b"second"),
            },
        )
        # Still exactly one submission row for that (application, document).
        subs = ApplicationDocumentSubmission.objects.filter(
            application=app, document=self.doc_required
        )
        self.assertEqual(subs.count(), 1)

    def test_upload_with_unknown_document_id_is_rejected(self):
        app = _approved_application(self.program)
        # An ID that doesn't belong to this program's docs:
        other_program = Program.objects.create(
            name="Other",
            year=2030,
            start_date=timezone.localdate() + datetime.timedelta(days=30),
            end_date=timezone.localdate() + datetime.timedelta(days=90),
            active=True,
        )
        foreign_doc = ProgramDocument.objects.create(
            program=other_program,
            name="Foreign doc",
            file=_make_blank_pdf("foreign.pdf"),
        )
        response = self.client.post(
            reverse("apply_step9", kwargs={"app_id": app.application_id}),
            {
                "document_id": foreign_doc.pk,
                "file": _make_signed_upload(),
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            ApplicationDocumentSubmission.objects.filter(application=app).count(),
            0,
        )

    def test_upload_with_missing_file_re_renders_form(self):
        app = _approved_application(self.program)
        response = self.client.post(
            reverse("apply_step9", kwargs={"app_id": app.application_id}),
            {"document_id": self.doc_required.pk},
        )
        # Form invalid → re-render page (200), no submission row created.
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            ApplicationDocumentSubmission.objects.filter(application=app).count(),
            0,
        )

    # -- Routing for APPROVED applicants ------------------------------------

    def test_resume_link_for_approved_application_goes_to_step9(self):
        app = _approved_application(self.program)
        response = self.client.get(
            reverse("apply_resume_link", kwargs={"app_id": app.application_id})
        )
        self.assertRedirects(
            response,
            reverse("apply_step9", kwargs={"app_id": app.application_id}),
            fetch_redirect_response=False,
        )

    def test_submitted_view_redirects_approved_application_to_step9(self):
        app = _approved_application(self.program)
        response = self.client.get(
            reverse("apply_submitted", kwargs={"app_id": app.application_id})
        )
        self.assertRedirects(
            response,
            reverse("apply_step9", kwargs={"app_id": app.application_id}),
            fetch_redirect_response=False,
        )

    # -- Approval email -----------------------------------------------------

    def test_send_application_approved_email_includes_parent_and_link(self):
        app = _approved_application(self.program)
        send_application_approved_email(app)
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertIn("parent@example.com", msg.to)
        # Body should reference the application ID and the resume link
        # (which routes to /step9/ for APPROVED apps).
        self.assertIn(app.application_id, msg.body)
        self.assertIn("/apply/r/", msg.body)
