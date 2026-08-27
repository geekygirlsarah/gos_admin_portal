"""Reproducer: the end-of-wizard confirmation page and the submission
confirmation email use "student" / parent language even when a *mentor*
applied (which has no student or parent information).

These tests assert that mentors see mentor-appropriate wording and that
student/parent applicants keep the existing wording.
"""

from __future__ import annotations

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from applications.models import Application
from applications.services import send_application_submitted_email
from programs.models import Program


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class MentorApplicationTextReproductionTests(TestCase):
    def setUp(self):
        self.program = Program.objects.create(name="Test Program", active=True)

    def _mentor_app(self):
        return Application.objects.create(
            applicant_type=Application.Type.MENTOR,
            email="newmentor@example.com",
            program=self.program,
            status=Application.Status.SUBMITTED,
            submitted_at="2025-01-01T00:00:00Z",
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
            status=Application.Status.SUBMITTED,
            submitted_at="2025-01-01T00:00:00Z",
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
                "step8-secondaryparent": {
                    "legal_first_name": "Mary",
                    "last_name": "Doe",
                },
            },
        )

    # -- Submitted (confirmation) page --------------------------------------

    def test_submitted_page_mentor_does_not_use_student_language(self):
        app = self._mentor_app()
        response = self.client.get(
            reverse("apply_submitted", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(response.status_code, 200)
        # Must NOT talk about "student" / "primary adult contact" for a mentor.
        self.assertNotContains(response, "student and the primary adult contact")
        self.assertNotContains(response, "to both the student")
        # Should show the mentor's name and mentor-appropriate wording.
        self.assertContains(response, "James Smith")
        self.assertContains(response, "Mentor")

    def test_submitted_page_student_keeps_student_language(self):
        app = self._student_app()
        response = self.client.get(
            reverse("apply_submitted", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Jane Doe")  # student
        self.assertContains(response, "John Doe")  # primary parent
        self.assertContains(response, "student and the primary adult contact")

    # -- Submission confirmation email --------------------------------------

    def test_submission_email_mentor_shows_mentor_not_student(self):
        app = self._mentor_app()
        send_application_submitted_email(app)
        self.assertEqual(len(mail.outbox), 1)
        body = mail.outbox[0].body
        # A mentor should be addressed as a Mentor, not told there is a Student.
        self.assertNotIn("Student:", body)
        self.assertIn("Mentor:", body)
        self.assertIn("James Smith", body)

    def test_submission_email_student_shows_student(self):
        app = self._student_app()
        send_application_submitted_email(app)
        self.assertEqual(len(mail.outbox), 1)
        body = mail.outbox[0].body
        self.assertIn("Student:", body)
        self.assertIn("Jane Doe", body)
