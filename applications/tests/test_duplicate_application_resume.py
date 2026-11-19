from __future__ import annotations

import tempfile
from datetime import timedelta
from unittest.mock import patch

from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from applications.models import Application, ApplicationDocumentSubmission
from applications.tests.access_helpers import verify_application_browser
from organizations.models import Organization
from programs.models import Program, ProgramDocument


class DuplicateApplicationTests(TestCase):
    def test_new_behavior_detects_duplicate_and_allows_resume(self):
        # 1. Existing draft application for test@example.com
        old_app = Application.objects.create(
            email="test@example.com",
            email_verified_at=None,
            current_step=5,  # Further along
            status=Application.Status.DRAFT,
        )

        # 2. Start a new application
        response = self.client.post(reverse("apply_start"))
        new_app = Application.objects.exclude(pk=old_app.pk).get()

        # 3. Set email to the same as old_app
        self.client.post(
            reverse("apply_step2", kwargs={"app_id": new_app.application_id}),
            {"applicant_type": Application.Type.PARENT, "email": "test@example.com"},
        )
        new_app.refresh_from_db()

        # 4. Verify email (Step 3)
        code = new_app.issue_otp()
        response = self.client.post(
            reverse("apply_step3", kwargs={"app_id": new_app.application_id}),
            {"code": code},
        )

        # NEW BEHAVIOR: it should redirect to duplicate-found page
        self.assertRedirects(
            response,
            reverse("apply_duplicate_found", kwargs={"app_id": new_app.application_id}),
            fetch_redirect_response=False,
        )
        verify_application_browser(self.client, new_app)

        # 5. Check the duplicate found page
        response = self.client.get(
            reverse("apply_duplicate_found", kwargs={"app_id": new_app.application_id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, old_app.application_id)

        # 6. Choose "resume"
        response = self.client.post(
            reverse("apply_duplicate_found", kwargs={"app_id": new_app.application_id}),
            {"action": "resume"},
        )

        # The old application must verify in this browser before the new one
        # is superseded.
        self.assertRedirects(
            response,
            reverse(
                "apply_browser_verification",
                kwargs={"app_id": old_app.application_id},
            ),
            fetch_redirect_response=False,
        )

        new_app.refresh_from_db()
        self.assertIsNone(new_app.superseded_at)
        verify_application_browser(self.client, old_app)
        new_app.refresh_from_db()
        self.assertIsNotNone(new_app.superseded_at)
        self.assertTrue(Application.objects.filter(pk=new_app.pk).exists())
        # Old app should still exist
        self.assertTrue(Application.objects.filter(pk=old_app.pk).exists())

    def test_new_behavior_allows_start_over(self):
        # 1. Existing draft application
        old_app = Application.objects.create(
            email="test2@example.com", status=Application.Status.DRAFT
        )

        # 2. New application
        app = Application.objects.create(
            email="test2@example.com", email_verified_at=timezone.now()
        )
        verify_application_browser(self.client, app)

        # 3. Choose "start over"
        response = self.client.post(
            reverse("apply_duplicate_found", kwargs={"app_id": app.application_id}),
            {"action": "start_over"},
        )

        # Should continue to Step 4 of the NEW application
        self.assertRedirects(
            response,
            reverse("apply_step4", kwargs={"app_id": app.application_id}),
            fetch_redirect_response=False,
        )

        old_app.refresh_from_db()
        self.assertIsNotNone(old_app.superseded_at)
        superseded_at = old_app.superseded_at
        self.assertTrue(Application.objects.filter(pk=old_app.pk).exists())
        # New app should still exist
        self.assertTrue(Application.objects.filter(pk=app.pk).exists())

        self.client.post(
            reverse("apply_duplicate_found", kwargs={"app_id": app.application_id}),
            {"action": "start_over"},
        )
        old_app.refresh_from_db()
        app.refresh_from_db()
        self.assertEqual(old_app.superseded_at, superseded_at)
        self.assertIsNone(app.superseded_at)
        self.assertTrue(Application.objects.filter(pk=old_app.pk).exists())
        self.assertTrue(Application.objects.filter(pk=app.pk).exists())

    def test_mentor_duplicate_resume_redirects_to_mentor_info(self):
        # 1. Existing draft MENTOR application
        old_app = Application.objects.create(
            email="mentor@example.com",
            applicant_type=Application.Type.MENTOR,
            status=Application.Status.DRAFT,
            email_verified_at=timezone.now(),
            current_step=4,
        )
        # Note: current_step=4 for mentors means they verified email already.
        # But here we are simulating that they started a NEW application and verified it.

        # 2. New application
        new_app = Application.objects.create(
            email="mentor@example.com",
            applicant_type=Application.Type.MENTOR,
            email_verified_at=timezone.now(),
        )
        verify_application_browser(self.client, new_app)

        # 3. Resume
        response = self.client.post(
            reverse("apply_duplicate_found", kwargs={"app_id": new_app.application_id}),
            {"action": "resume"},
        )

        # Verify the existing application's browser before resuming it.
        self.assertRedirects(
            response,
            reverse(
                "apply_browser_verification",
                kwargs={"app_id": old_app.application_id},
            ),
            fetch_redirect_response=False,
        )
        verify_application_browser(self.client, old_app)
        new_app.refresh_from_db()
        self.assertIsNotNone(new_app.superseded_at)

    def test_mentor_start_over_redirects_to_mentor_info(self):
        # 1. New mentor application
        app = Application.objects.create(
            email="mentor2@example.com",
            applicant_type=Application.Type.MENTOR,
            email_verified_at=timezone.now(),
        )
        verify_application_browser(self.client, app)
        # Existing one
        old_app = Application.objects.create(
            email="mentor2@example.com", status=Application.Status.DRAFT
        )

        # 2. Start over
        response = self.client.post(
            reverse("apply_duplicate_found", kwargs={"app_id": app.application_id}),
            {"action": "start_over"},
        )

        # Should continue to mentor_info step (not step 4)
        self.assertRedirects(
            response,
            reverse("apply_mentor_info", kwargs={"app_id": app.application_id}),
            fetch_redirect_response=False,
        )
        old_app.refresh_from_db()
        self.assertIsNotNone(old_app.superseded_at)


class NonDestructiveApplicationRestartTests(TestCase):
    def make_application(self, **kwargs):
        defaults = {
            "email": "shared@example.test",
            "email_verified_at": timezone.now(),
            "current_step": 5,
            "status": Application.Status.DRAFT,
            "applicant_progress_at": timezone.now() - timedelta(hours=4),
            "data": {"step5-student": {"legal_first_name": "Saved"}},
        }
        defaults.update(kwargs)
        return Application.objects.create(**defaults)

    def test_start_over_supersedes_all_matching_drafts_and_preserves_signed_bytes(self):
        progress_at = timezone.now() - timedelta(days=4)
        previous = self.make_application(
            applicant_progress_at=progress_at,
            data={"step5-student": {"legal_first_name": "Old answer"}},
        )
        completed = self.make_application(status=Application.Status.SUBMITTED)
        previous_updated_at = previous.updated_at
        current = self.make_application(current_step=4)
        organization = previous.organization
        program = Program.objects.create(
            name="Restart Test Program", organization=organization
        )
        previous.program = program
        previous.save(update_fields=["program"])
        document = ProgramDocument.objects.create(
            program=program,
            name="Permission form",
            file=SimpleUploadedFile("permission.pdf", b"blank form"),
            is_required=True,
        )

        with tempfile.TemporaryDirectory() as media_root:
            with override_settings(MEDIA_ROOT=media_root):
                submission = ApplicationDocumentSubmission.objects.create(
                    organization=organization,
                    application=previous,
                    document=document,
                    file=SimpleUploadedFile(
                        "signed.pdf", b"private signed bytes", "application/pdf"
                    ),
                )
                verify_application_browser(self.client, current)
                response = self.client.post(
                    reverse(
                        "apply_duplicate_found",
                        kwargs={"app_id": current.application_id},
                    ),
                    {"action": "start_over"},
                )

                previous.refresh_from_db()
                completed.refresh_from_db()
                current.refresh_from_db()
                self.assertIsNotNone(previous.superseded_at)
                self.assertIsNone(completed.superseded_at)
                self.assertIsNone(current.superseded_at)
                self.assertEqual(
                    previous.data["step5-student"]["legal_first_name"], "Old answer"
                )
                self.assertEqual(previous.applicant_progress_at, progress_at)
                self.assertEqual(previous.updated_at, previous_updated_at)
                self.assertEqual(previous.status, Application.Status.DRAFT)
                self.assertTrue(
                    ApplicationDocumentSubmission.objects.filter(
                        pk=submission.pk
                    ).exists()
                )
                with submission.file.open("rb") as saved_file:
                    self.assertEqual(saved_file.read(), b"private signed bytes")
                self.assertEqual(response.status_code, 302)

    def test_start_over_keeps_unverified_guardian_handoff_active_and_reports_partial(
        self,
    ):
        old_draft = self.make_application()
        handoff = self.make_application(
            applicant_type=Application.Type.STUDENT,
            status=Application.Status.AWAITING_PARENT,
            current_step=7,
            data={"step7_handoff": {"parent_email": "guardian@example.test"}},
        )
        handoff.issue_handoff_token()
        current = self.make_application(current_step=4)
        verify_application_browser(self.client, current)

        response = self.client.post(
            reverse("apply_duplicate_found", kwargs={"app_id": current.application_id}),
            {"action": "start_over"},
        )

        old_draft.refresh_from_db()
        handoff.refresh_from_db()
        self.assertIsNotNone(old_draft.superseded_at)
        self.assertIsNone(handoff.superseded_at)
        self.assertTrue(
            any(
                "additional verification is required" in str(message)
                for message in get_messages(response.wsgi_request)
            )
        )

    def test_guardian_verified_handoff_can_be_superseded(self):
        handoff = self.make_application(
            applicant_type=Application.Type.STUDENT,
            status=Application.Status.AWAITING_PARENT,
            current_step=7,
            data={"step7_handoff": {"parent_email": "guardian@example.test"}},
        )
        handoff.issue_handoff_token()
        current = self.make_application(current_step=4)
        verify_application_browser(
            self.client, handoff, expected_recipient="guardian@example.test"
        )
        verify_application_browser(self.client, current)

        self.client.post(
            reverse("apply_duplicate_found", kwargs={"app_id": current.application_id}),
            {"action": "start_over"},
        )

        handoff.refresh_from_db()
        self.assertIsNotNone(handoff.superseded_at)

    def test_resume_supersedes_new_draft_only_after_target_browser_verification(self):
        previous = self.make_application(
            email_verified_at=None,
            data={"step5-student": {"legal_first_name": "Existing"}},
        )
        current = self.make_application(current_step=4)
        verify_application_browser(self.client, current)

        response = self.client.post(
            reverse("apply_duplicate_found", kwargs={"app_id": current.application_id}),
            {"action": "resume"},
        )

        self.assertRedirects(
            response,
            reverse(
                "apply_browser_verification",
                kwargs={"app_id": previous.application_id},
            ),
            fetch_redirect_response=False,
        )
        current.refresh_from_db()
        self.assertIsNone(current.superseded_at)
        verification_url = reverse(
            "apply_browser_verification", kwargs={"app_id": previous.application_id}
        )
        with patch(
            "applications.views.browser.generate_otp_code", return_value="123456"
        ):
            with patch("applications.views.browser.send_otp_email"):
                self.client.post(verification_url, {"action": "send"})
        wrong_code_response = self.client.post(verification_url, {"code": "654321"})
        self.assertEqual(wrong_code_response.status_code, 200)
        self.assertRedirects(
            self.client.get(
                reverse("apply_step5", kwargs={"app_id": previous.application_id})
            ),
            verification_url,
            fetch_redirect_response=False,
        )
        current.refresh_from_db()
        previous.refresh_from_db()
        self.assertIsNone(current.superseded_at)
        self.assertIsNone(previous.superseded_at)
        verify_application_browser(self.client, previous)
        current.refresh_from_db()
        previous.refresh_from_db()
        self.assertIsNotNone(current.superseded_at)
        self.assertTrue(Application.objects.filter(pk=current.pk).exists())
        self.assertEqual(previous.data["step5-student"]["legal_first_name"], "Existing")
        self.assertEqual(
            self.client.get(
                reverse("apply_step5", kwargs={"app_id": current.application_id})
            ).status_code,
            302,
        )

    def test_resume_revalidation_failure_preserves_both_applications(self):
        previous = self.make_application()
        current = self.make_application(current_step=4)
        verify_application_browser(self.client, current)
        self.client.post(
            reverse("apply_duplicate_found", kwargs={"app_id": current.application_id}),
            {"action": "resume"},
        )
        previous.status = Application.Status.DECLINED
        previous.save(update_fields=["status"])

        verify_application_browser(self.client, previous)

        current.refresh_from_db()
        previous.refresh_from_db()
        self.assertIsNone(current.superseded_at)
        self.assertIsNone(previous.superseded_at)
        self.assertTrue(Application.objects.filter(pk=current.pk).exists())
        self.assertTrue(Application.objects.filter(pk=previous.pk).exists())

    def test_start_over_does_not_supersede_matches_from_another_tenant(self):
        current = self.make_application(current_step=4)
        local_previous = self.make_application()
        other_organization = Organization.objects.create(
            name="Other Restart Organization",
            slug="other-restart-organization",
            subdomain="other-restart-organization",
            is_active=True,
        )
        foreign_previous = self.make_application(
            organization=other_organization,
        )
        verify_application_browser(self.client, current)

        self.client.post(
            reverse("apply_duplicate_found", kwargs={"app_id": current.application_id}),
            {"action": "start_over"},
        )

        local_previous.refresh_from_db()
        foreign_previous.refresh_from_db()
        self.assertIsNotNone(local_previous.superseded_at)
        self.assertIsNone(foreign_previous.superseded_at)

    def test_resume_handoff_waits_for_guardian_otp(self):
        handoff = self.make_application(
            applicant_type=Application.Type.STUDENT,
            status=Application.Status.AWAITING_PARENT,
            current_step=7,
            data={"step7_handoff": {"parent_email": "guardian@example.test"}},
        )
        handoff.issue_handoff_token()
        current = self.make_application(current_step=4)
        verify_application_browser(self.client, current)

        response = self.client.post(
            reverse("apply_duplicate_found", kwargs={"app_id": current.application_id}),
            {"action": "resume"},
        )
        self.assertRedirects(
            response,
            reverse(
                "apply_browser_verification",
                kwargs={"app_id": handoff.application_id},
            ),
            fetch_redirect_response=False,
        )
        current.refresh_from_db()
        self.assertIsNone(current.superseded_at)

        verify_application_browser(
            self.client, handoff, expected_recipient="guardian@example.test"
        )
        current.refresh_from_db()
        self.assertIsNotNone(current.superseded_at)
        self.assertEqual(
            self.client.get(
                reverse("apply_step7", kwargs={"app_id": handoff.application_id})
            ).status_code,
            200,
        )
