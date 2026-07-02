"""End-to-end-ish tests for the application wizard views (Steps 1-4)."""

from __future__ import annotations

import datetime

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from applications.models import APP_ID_LENGTH, Application, SiteSettings
from programs.models import Program


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class WizardFlowTests(TestCase):
    def setUp(self):
        today = timezone.localdate()
        self.future_program = Program.objects.create(
            name="Spring 2030",
            description="An upcoming program.",
            start_date=today + datetime.timedelta(days=60),
            end_date=today + datetime.timedelta(days=120),
            active=True,
        )
        self.current_program = Program.objects.create(
            name="Right Now",
            start_date=today - datetime.timedelta(days=10),
            end_date=today + datetime.timedelta(days=10),
            active=True,
        )
        mail.outbox = []

    # --- Step 1 ---------------------------------------------------------

    def test_step1_get_renders_welcome(self):
        response = self.client.get(reverse("apply_start"))
        self.assertEqual(response.status_code, 200)
        # Default site-settings welcome text is shown.
        self.assertContains(response, "Welcome")
        self.assertContains(response, "Application ID")

    def test_step1_post_starts_new_application_and_redirects_to_step2(self):
        response = self.client.post(reverse("apply_start"))
        self.assertEqual(response.status_code, 302)
        app = Application.objects.get()
        self.assertEqual(len(app.application_id), APP_ID_LENGTH)
        self.assertEqual(
            response["Location"],
            reverse("apply_step2", kwargs={"app_id": app.application_id}),
        )

    def test_step1_resume_with_unknown_id_shows_error(self):
        response = self.client.post(
            reverse("apply_resume"), {"application_id": "ZZZZZZZZ"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "couldn&#x27;t find")

    def test_step1_resume_with_known_id_redirects_to_current_step(self):
        # current_step=3 means email verify is next (step 3 in the URL)
        app = Application.objects.create(current_step=3)
        response = self.client.post(
            reverse("apply_resume"),
            {"application_id": app.application_id},
        )
        self.assertRedirects(
            response,
            reverse("apply_step3", kwargs={"app_id": app.application_id}),
            fetch_redirect_response=False,
        )

    def test_resume_link_view_redirects_to_current_step(self):
        app = Application.objects.create(current_step=2)
        response = self.client.get(
            reverse("apply_resume_link", kwargs={"app_id": app.application_id})
        )
        self.assertRedirects(
            response,
            reverse("apply_step2", kwargs={"app_id": app.application_id}),
            fetch_redirect_response=False,
        )

    # --- Step 2 ---------------------------------------------------------

    def test_step2_parent_requires_email(self):
        app = Application.objects.create()
        response = self.client.post(
            reverse("apply_step2", kwargs={"app_id": app.application_id}),
            {"applicant_type": "parent", "email": ""},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "An email address is required")

    def test_step2_student_with_email_advances(self):
        app = Application.objects.create()
        response = self.client.post(
            reverse("apply_step2", kwargs={"app_id": app.application_id}),
            {"applicant_type": "student", "email": "kid@example.com"},
        )
        self.assertRedirects(
            response,
            reverse("apply_step3", kwargs={"app_id": app.application_id}),
            fetch_redirect_response=False,
        )
        app.refresh_from_db()
        self.assertEqual(app.email, "kid@example.com")
        self.assertEqual(app.applicant_type, "student")
        self.assertGreaterEqual(app.current_step, 3)
        # No email sent yet — it's deferred until Step 4 landing.
        self.assertEqual(len(mail.outbox), 0)

    def test_step2_student_without_email_redirects_to_start_with_message(self):
        app = Application.objects.create()
        response = self.client.post(
            reverse("apply_step2", kwargs={"app_id": app.application_id}),
            {"applicant_type": "student", "email": ""},
        )
        self.assertRedirects(
            response, reverse("apply_start"), fetch_redirect_response=False
        )

    # --- Step 4 (Program Select) -----------------------------------------

    def test_step4_lists_future_and_current_programs_separately(self):
        app = Application.objects.create(
            applicant_type="parent",
            email="parent@example.com",
            current_step=4,
            email_verified_at=timezone.now(),
            status=Application.Status.EMAIL_VERIFIED,
        )
        response = self.client.get(
            reverse("apply_step4", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.future_program.name)
        self.assertContains(response, self.current_program.name)
        self.assertContains(response, "Applications for these programs are closed")

    def test_step4_shows_accordion_for_program_details(self):
        # Ensure the future program has a description (blurb)
        self.future_program.description = "This is a detailed blurb about the program."
        self.future_program.save()

        app = Application.objects.create(
            applicant_type="parent",
            email="parent@example.com",
            current_step=4,
            email_verified_at=timezone.now(),
            status=Application.Status.EMAIL_VERIFIED,
        )
        response = self.client.get(
            reverse("apply_step4", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(response.status_code, 200)
        # Contains an accordion/collapse control to reveal details
        self.assertContains(response, "data-bs-toggle=\"collapse\"")
        # Expect an accordion container for grouping
        self.assertContains(response, "accordion")
        # The description should be present within the HTML (likely inside the collapsed area)
        self.assertContains(response, "This is a detailed blurb about the program.")

    def test_step4_post_only_accepts_future_programs(self):
        app = Application.objects.create(
            applicant_type="parent",
            email="parent@example.com",
            current_step=4,
            email_verified_at=timezone.now(),
            status=Application.Status.EMAIL_VERIFIED,
        )
        # Trying to pick a current (closed) program must fail validation.
        response = self.client.post(
            reverse("apply_step4", kwargs={"app_id": app.application_id}),
            {"program": self.current_program.pk},
        )
        self.assertEqual(response.status_code, 200)
        app.refresh_from_db()
        self.assertIsNone(app.program)

    def test_step4_post_with_future_program_advances(self):
        app = Application.objects.create(
            applicant_type="parent",
            email="parent@example.com",
            current_step=4,
            email_verified_at=timezone.now(),
            status=Application.Status.EMAIL_VERIFIED,
        )
        response = self.client.post(
            reverse("apply_step4", kwargs={"app_id": app.application_id}),
            {"program": self.future_program.pk},
        )
        self.assertRedirects(
            response,
            reverse("apply_continue", kwargs={"app_id": app.application_id}),
            fetch_redirect_response=False,
        )
        app.refresh_from_db()
        self.assertEqual(app.program_id, self.future_program.pk)

    def test_step4_displays_grade_range(self):
        self.future_program.grade_range_start = 4
        self.future_program.grade_range_end = 6
        self.future_program.save()

        app = Application.objects.create(
            applicant_type="parent",
            email="parent@example.com",
            current_step=4,
            email_verified_at=timezone.now(),
            status=Application.Status.EMAIL_VERIFIED,
        )
        response = self.client.get(
            reverse("apply_step4", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "4th–6th Grade")

    # --- Step 3 (Email Verify) -------------------------------------------

    def test_step3_get_issues_otp_and_emails_it(self):
        app = Application.objects.create(
            applicant_type="parent",
            email="parent@example.com",
            current_step=3,
        )
        response = self.client.get(
            reverse("apply_step3", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(response.status_code, 200)
        app.refresh_from_db()
        self.assertTrue(app.otp_hash)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("verification code", mail.outbox[0].subject.lower())
        # The email body contains the plain code and resume link.
        body = mail.outbox[0].body
        self.assertIn("Thanks for starting an application", body)
        self.assertIn(app.application_id, body)
        self.assertIn("Resume link:", body)
        # Extract the 6-digit code from the email body for use in next step.
        import re

        m = re.search(r"\b(\d{6})\b", body)
        self.assertIsNotNone(m)

    def test_step3_post_with_correct_code_advances_to_step4(self):
        app = Application.objects.create(
            applicant_type="parent",
            email="parent@example.com",
            current_step=3,
        )
        code = app.issue_otp()
        response = self.client.post(
            reverse("apply_step3", kwargs={"app_id": app.application_id}),
            {"code": code},
        )
        self.assertRedirects(
            response,
            reverse("apply_step4", kwargs={"app_id": app.application_id}),
            fetch_redirect_response=False,
        )
        app.refresh_from_db()
        self.assertIsNotNone(app.email_verified_at)
        self.assertEqual(app.status, Application.Status.EMAIL_VERIFIED)

    def test_step3_post_with_wrong_code_stays_on_page(self):
        app = Application.objects.create(
            applicant_type="parent",
            email="parent@example.com",
            current_step=3,
        )
        app.issue_otp()
        response = self.client.post(
            reverse("apply_step3", kwargs={"app_id": app.application_id}),
            {"code": "000000"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "didn&#x27;t match")

    def test_step3_resend_issues_new_code(self):
        app = Application.objects.create(
            applicant_type="parent",
            email="parent@example.com",
            current_step=3,
        )
        old_code = app.issue_otp()
        old_hash = Application.objects.get(pk=app.pk).otp_hash
        mail.outbox = []
        response = self.client.post(
            reverse("apply_step3_resend", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(response.status_code, 302)
        app.refresh_from_db()
        self.assertNotEqual(app.otp_hash, old_hash)
        # Old code should no longer verify.
        self.assertFalse(app.verify_otp(old_code))
        self.assertEqual(len(mail.outbox), 1)

    # --- Continue placeholder ------------------------------------------

    def test_continue_requires_verified_email(self):
        app = Application.objects.create(
            applicant_type="parent",
            email="parent@example.com",
            program=self.future_program,
            current_step=5,
        )
        response = self.client.get(
            reverse("apply_continue", kwargs={"app_id": app.application_id})
        )
        self.assertRedirects(
            response,
            reverse("apply_step4", kwargs={"app_id": app.application_id}),
            fetch_redirect_response=False,
        )

    def test_continue_redirects_to_current_step_when_verified(self):
        app = Application.objects.create(
            applicant_type="parent",
            email="parent@example.com",
            program=self.future_program,
            current_step=5,
            email_verified_at=timezone.now(),
            status=Application.Status.EMAIL_VERIFIED,
        )
        response = self.client.get(
            reverse("apply_continue", kwargs={"app_id": app.application_id})
        )
        self.assertRedirects(
            response,
            reverse("apply_step5", kwargs={"app_id": app.application_id}),
            fetch_redirect_response=False,
        )


class CustomWelcomeMessageTests(TestCase):
    def test_step1_uses_custom_welcome_message(self):
        s = SiteSettings.load()
        s.welcome_message = "Hello prospective robot builder!"
        s.save()
        response = self.client.get(reverse("apply_start"))
        self.assertContains(response, "Hello prospective robot builder!")
