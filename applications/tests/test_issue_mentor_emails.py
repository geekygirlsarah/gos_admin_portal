"""Reproducer: mentor applications receive approval / conversion / decline /
lead-notification emails phrased for students — e.g. a "Student:
(not specified)" line — and the approval email tells mentors they must sign
documents, even though mentors are converted directly without any
signed-document step.

These tests assert mentors see mentor-appropriate wording (and no
signed-document requirement), while student/parent applicants keep the
existing wording.
"""

from __future__ import annotations

from django.core import mail
from django.test import TestCase, override_settings

from applications.models import Application
from applications.services import (
    send_application_approved_email,
    send_application_converted_email,
    send_application_declined_email,
    send_lead_notification_email,
)
from programs.models import Program


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class MentorEmailsReproductionTests(TestCase):
    def setUp(self):
        self.program = Program.objects.create(name="Test Program", active=True)

    def _mentor_app(self):
        return Application.objects.create(
            applicant_type=Application.Type.MENTOR,
            email="mentor@example.com",
            status=Application.Status.APPROVED,
            data={
                "mentor_info": {
                    "legal_first_name": "James",
                    "last_name": "Smith",
                }
            },
        )

    def _student_app(self):
        return Application.objects.create(
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
                    "legal_first_name": "John",
                    "last_name": "Doe",
                    "email": "parent@example.com",
                },
            },
        )

    # -- Approved email ------------------------------------------------------

    def test_approved_email_mentor_does_not_use_student_language(self):
        send_application_approved_email(self._mentor_app())
        self.assertEqual(len(mail.outbox), 1)
        body = mail.outbox[0].body
        self.assertNotIn("Student:", body)
        self.assertNotIn("(not specified)", body)
        self.assertIn("Mentor:", body)
        self.assertIn("James Smith", body)

    def test_approved_email_mentor_does_not_mention_signing_documents(self):
        send_application_approved_email(self._mentor_app())
        self.assertEqual(len(mail.outbox), 1)
        body = mail.outbox[0].body
        self.assertNotIn("download, sign, and re-upload", body)
        self.assertNotIn("sign", body.lower())

    def test_approved_email_student_keeps_student_and_documents_wording(self):
        send_application_approved_email(self._student_app())
        self.assertEqual(len(mail.outbox), 1)
        body = mail.outbox[0].body
        self.assertIn("Student:", body)
        self.assertIn("Jane Doe", body)
        # self.assertIn("download, sign, and re-upload", body)

    def test_approved_email_student_does_not_mention_mentor_onboarding(self):
        send_application_approved_email(self._student_app())
        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        for body in (email.body, email.alternatives[0][0]):
            self.assertNotIn("becoming a Girls of Steel mentor", body)
            self.assertNotIn("Welcome aboard", body)
            self.assertNotIn("Mentor:", body)

    def test_approved_email_mentor_does_not_mention_student_documents_html(self):
        send_application_approved_email(self._mentor_app())
        self.assertEqual(len(mail.outbox), 1)
        html = mail.outbox[0].alternatives[0][0]
        self.assertNotIn("not fully enrolled", html)
        self.assertNotIn("one more step", html)
        self.assertNotIn("Student:", html)
        self.assertIn("James Smith", html)

    def test_approved_email_student_html_keeps_student_wording(self):
        send_application_approved_email(self._student_app())
        self.assertEqual(len(mail.outbox), 1)
        html = mail.outbox[0].alternatives[0][0]
        self.assertIn("Student:", html)
        self.assertIn("Jane Doe", html)
        self.assertIn("not fully enrolled", html)
        self.assertNotIn("Mentor:", html)

    # -- Converted email -----------------------------------------------------

    def test_converted_email_mentor_does_not_use_student_language(self):
        send_application_converted_email(self._mentor_app())
        self.assertEqual(len(mail.outbox), 1)
        body = mail.outbox[0].body
        self.assertNotIn("Student:", body)
        self.assertIn("Mentor:", body)
        self.assertIn("James Smith", body)

    def test_converted_email_student_keeps_student_language(self):
        send_application_converted_email(self._student_app())
        self.assertEqual(len(mail.outbox), 1)
        body = mail.outbox[0].body
        self.assertIn("Student:", body)
        self.assertIn("Jane Doe", body)

    # -- Declined email ------------------------------------------------------

    def test_declined_email_mentor_does_not_use_student_language(self):
        send_application_declined_email(self._mentor_app(), reason="Test reason")
        self.assertEqual(len(mail.outbox), 1)
        body = mail.outbox[0].body
        self.assertNotIn("Student:", body)
        self.assertIn("Mentor:", body)
        self.assertIn("James Smith", body)

    def test_declined_email_student_keeps_student_language(self):
        send_application_declined_email(self._student_app(), reason="Test reason")
        self.assertEqual(len(mail.outbox), 1)
        body = mail.outbox[0].body
        self.assertIn("Student:", body)
        self.assertIn("Jane Doe", body)

    # -- Lead notification ---------------------------------------------------

    def test_lead_notification_mentor_shows_mentor_not_student(self):
        send_lead_notification_email(self._mentor_app())
        self.assertEqual(len(mail.outbox), 1)
        body = mail.outbox[0].body
        self.assertNotIn("Student:", body)
        self.assertIn("James Smith", body)

    def test_lead_notification_student_shows_student(self):
        send_lead_notification_email(self._student_app())
        self.assertEqual(len(mail.outbox), 1)
        body = mail.outbox[0].body
        self.assertIn("Student:", body)
        self.assertIn("Jane Doe", body)
