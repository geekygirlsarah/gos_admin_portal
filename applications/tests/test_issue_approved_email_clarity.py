"""Reproducer: the approved-application email for students/parents reads as a
done deal ("Great news — approved!") and buries the required action near the
bottom, so applicants barely notice the documents must be signed *before* the
student is officially in the program.

These tests assert the email makes the next actions prominent and clearly
states enrollment is not final until the signed documents are received.
"""

from __future__ import annotations

from django.core import mail
from django.test import TestCase, override_settings

from applications.models import Application
from applications.services import send_application_approved_email
from programs.models import Program


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class ApprovedEmailClarityTests(TestCase):
    def setUp(self):
        self.program = Program.objects.create(name="Test Program", active=True)
        self.app = Application.objects.create(
            applicant_type=Application.Type.STUDENT,
            email="student@example.com",
            program=self.program,
            status=Application.Status.APPROVED,
            data={
                "step5-student": {
                    "legal_first_name": "Jane",
                    "last_name": "Doe",
                },
                "step7-primaryparent": {
                    "first_name": "John",
                    "last_name": "Doe",
                    "email": "parent@example.com",
                },
            },
        )

    def test_email_highlights_a_required_next_step(self):
        send_application_approved_email(self.app)
        self.assertEqual(len(mail.outbox), 1)
        body = mail.outbox[0].body
        self.assertIn("one more step", body)
        self.assertIn("download, sign, and re-upload", body.lower())

    def test_email_says_enrollment_is_not_final_until_documents_signed(self):
        send_application_approved_email(self.app)
        self.assertEqual(len(mail.outbox), 1)
        body = mail.outbox[0].body
        self.assertIn("not fully enrolled", body)
        self.assertIn("until", body)

    def test_subject_mentions_action_needed_to_finish_enrollment(self):
        send_application_approved_email(self.app)
        self.assertEqual(len(mail.outbox), 1)
        subject = mail.outbox[0].subject.lower()
        self.assertIn("approved", subject)
        self.assertTrue(
            any(word in subject for word in ("sign", "action", "step", "enroll")),
            f"subject does not hint at the required next step: {subject!r}",
        )
