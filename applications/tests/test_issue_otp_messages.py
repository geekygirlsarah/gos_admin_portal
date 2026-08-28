"""Reproducer tests for the OTP "expired" mislabeling issue.

An applicant who entered a fresh (1-2 minute old) code was told it had
"expired". Two root causes are covered here:

1. ``verify_otp`` only returned a bool, so the view could not tell a wrong
   code apart from an expired one and showed one conflated message that said
   "didn't match or has expired".
2. Step 2 did not issue the OTP, leaving Step 3's GET handler to generate one
   whenever the applicant arrived without a pending code. That left a window
   where a reload/second visit could issue a fresh code and invalidate the one
   already sitting in the applicant's inbox.
"""

from __future__ import annotations

from datetime import timedelta

from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from applications.models import Application, OtpVerifyResult


class OtpVerifyResultTests(TestCase):
    def test_wrong_code_is_invalid_not_expired(self):
        app = Application.objects.create(email="user@example.com")
        app.issue_otp()
        self.assertIs(app.verify_otp("000000"), OtpVerifyResult.INVALID)

    def test_expired_code_is_expired(self):
        app = Application.objects.create(email="user@example.com")
        code = app.issue_otp()
        app.otp_expires_at = timezone.now() - timedelta(minutes=1)
        app.save(update_fields=["otp_expires_at"])
        self.assertIs(app.verify_otp(code), OtpVerifyResult.EXPIRED)

    def test_no_pending_code(self):
        app = Application.objects.create(email="user@example.com")
        self.assertIs(app.verify_otp("123456"), OtpVerifyResult.NO_CODE)

    def test_attempt_cap_is_too_many_attempts(self):
        app = Application.objects.create(email="user@example.com")
        code = app.issue_otp()
        for _ in range(11):
            app.verify_otp("000000")
        self.assertIs(app.verify_otp(code), OtpVerifyResult.TOO_MANY_ATTEMPTS)


class Step3ErrorMessageTests(TestCase):
    def test_wrong_code_shows_did_not_match_not_expired(self):
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
        self.assertContains(response, "didn")
        self.assertNotContains(response, "expired")

    def test_expired_code_shows_expired_message(self):
        app = Application.objects.create(
            applicant_type="parent",
            email="parent@example.com",
            current_step=3,
        )
        code = app.issue_otp()
        app.otp_expires_at = timezone.now() - timedelta(minutes=1)
        app.save(update_fields=["otp_expires_at"])
        response = self.client.post(
            reverse("apply_step3", kwargs={"app_id": app.application_id}),
            {"code": code},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "expired")

    def test_no_pending_code_prompts_request_new(self):
        app = Application.objects.create(
            applicant_type="parent",
            email="parent@example.com",
            current_step=3,
        )
        response = self.client.post(
            reverse("apply_step3", kwargs={"app_id": app.application_id}),
            {"code": "123456"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "request a new code")

    def test_success_advances(self):
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
        self.assertEqual(response.status_code, 302)


class Step2IssuesOtpTests(TestCase):
    def test_step2_post_issues_and_emails_otp(self):
        app = Application.objects.create(applicant_type="parent")
        response = self.client.post(
            reverse("apply_step2", kwargs={"app_id": app.application_id}),
            {"applicant_type": "parent", "email": "parent@example.com"},
        )
        self.assertRedirects(
            response,
            reverse("apply_step3", kwargs={"app_id": app.application_id}),
            fetch_redirect_response=False,
        )
        app.refresh_from_db()
        self.assertTrue(app.otp_hash)
        self.assertEqual(len(mail.outbox), 1)

    def test_step3_get_does_not_regenerate_pending_code(self):
        app = Application.objects.create(
            applicant_type="parent",
            email="parent@example.com",
            current_step=3,
        )
        code = app.issue_otp()
        original_hash = app.otp_hash
        mail.outbox = []
        response = self.client.get(
            reverse("apply_step3", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(response.status_code, 200)
        app.refresh_from_db()
        self.assertEqual(app.otp_hash, original_hash)
        self.assertEqual(len(mail.outbox), 0)
        self.assertTrue(app.verify_otp(code))
