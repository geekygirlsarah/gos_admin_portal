"""Tests for the public guest form submission views (email confirmation)."""

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from guest_forms.models import GuestForm, GuestFormSubmission


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class GuestFormSubmissionEmailTests(TestCase):
    """Regression: submitting a guest form must email the submitter a confirmation."""

    @classmethod
    def setUpTestData(cls):
        cls.guest_form = GuestForm.objects.create(
            form_type="adult",
            name="Adult Waiver",
            slug="adult-waiver",
            file=SimpleUploadedFile(
                "form.pdf", b"%PDF-1.4 fake pdf", content_type="application/pdf"
            ),
        )

    def _pdf(self):
        return SimpleUploadedFile(
            "signed.pdf", b"%PDF-1.4 fake pdf", content_type="application/pdf"
        )

    def _valid_post_data(self):
        return {
            "participant_first_name": "Alex",
            "participant_last_name": "Smith",
            "email": "alex@example.com",
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

    def test_submission_sends_confirmation_email(self):
        resp = self.client.post(
            reverse("guest_form_detail", args=[self.guest_form.slug]),
            self._valid_post_data(),
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            resp.url, reverse("guest_form_submitted", args=[self.guest_form.slug])
        )
        self.assertTrue(
            GuestFormSubmission.objects.filter(
                guest_form=self.guest_form, email="alex@example.com"
            ).exists()
        )

        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertEqual(email.to, ["alex@example.com"])
        self.assertIn("Adult Waiver", email.subject)
        self.assertIn("Alex Smith", email.body)
        self.assertIn("Adult Waiver", email.body)

    def test_invalid_submission_sends_no_email(self):
        data = self._valid_post_data()
        data["agreed_legal_notices"] = ""
        resp = self.client.post(
            reverse("guest_form_detail", args=[self.guest_form.slug]), data
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(mail.outbox), 0)
