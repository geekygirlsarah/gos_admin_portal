from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase

from applications.models import Application
from applications.services import (
    send_application_approved_email,
    send_application_declined_email,
    send_application_submitted_email,
    send_lead_notification_email,
    send_parent_handoff_email,
)
from organizations.models import Organization, UserOrganizationMembership
from programs.models import Program


class EmailNamesTest(TestCase):
    def setUp(self):
        self.organization = Organization.objects.get(slug="gos")
        self.program = Program.objects.create(
            organization=self.organization,
            name="Test Program",
            active=True,
        )
        lead = get_user_model().objects.create_user(
            username="email-names-lead",
            email="email-names-lead@example.test",
        )
        UserOrganizationMembership.objects.create(
            user=lead,
            organization=self.organization,
            role="LeadMentor",
            is_active=True,
        )
        self.student_app = Application.objects.create(
            organization=self.organization,
            applicant_type=Application.Type.STUDENT,
            email="student@example.com",
            program=self.program,
            status=Application.Status.SUBMITTED,
            data={
                "step5-student": {
                    "legal_first_name": "Jane",
                    "last_name": "Doe",
                },
                "step7-primaryparent": {
                    "legal_first_name": "John",
                    "last_name": "Doe",
                },
                "step8-secondaryparent": {
                    "legal_first_name": "Mary",
                    "last_name": "Doe",
                },
            },
        )
        self.mentor_app = Application.objects.create(
            organization=self.organization,
            applicant_type=Application.Type.MENTOR,
            email="mentor@example.com",
            program=self.program,
            status=Application.Status.SUBMITTED,
            data={
                "mentor_info": {
                    "legal_first_name": "James",
                    "last_name": "Smith",
                }
            },
        )

    def test_application_submitted_email_contains_names(self):
        send_application_submitted_email(self.student_app)
        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertIn("Jane Doe", email.body)
        self.assertIn("John Doe", email.body)
        self.assertIn("Mary Doe", email.body)

    def test_application_approved_email_contains_names(self):
        send_application_approved_email(self.student_app)
        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertIn("Jane Doe", email.body)

    def test_application_declined_email_contains_names(self):
        send_application_declined_email(self.student_app, reason="Test reason")
        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertIn("Jane Doe", email.body)

    def test_lead_notification_email_contains_names(self):
        send_lead_notification_email(self.student_app)
        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertIn("Jane Doe", email.body)
        self.assertIn("John Doe", email.body)

    def test_parent_handoff_email_contains_names(self):
        send_parent_handoff_email(self.student_app, "parent@example.com")
        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertIn("Jane Doe", email.body)

    def test_mentor_submitted_email_contains_names(self):
        send_application_submitted_email(self.mentor_app)
        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertIn("James Smith", email.body)
