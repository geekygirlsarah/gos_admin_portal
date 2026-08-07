"""Tests for email subaddressing validation and email sender names."""

import re

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from applications.forms import ParentInfoForm
from applications.models import Application
from applications.services import (
    send_application_approved_email,
    send_application_declined_email,
    send_application_submitted_email,
    send_lead_notification_email,
    send_otp_email,
    send_parent_handoff_email,
)
from programs.models import Program


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class EmailSenderNameReproductionTest(TestCase):
    def test_default_from_email_with_name(self):
        app = Application.objects.create(email="test@example.com")
        with override_settings(
            DEFAULT_FROM_EMAIL="noreply@girlsofsteelrobotics.org",
            DEFAULT_FROM_NAME="Girls of Steel Admin",
        ):
            send_otp_email(app, "123456")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(
            mail.outbox[0].from_email,
            '"Girls of Steel Admin" <noreply@girlsofsteelrobotics.org>',
        )


class EmailSubaddressingValidationReproductionTests(TestCase):
    """Test that we correctly allow/prevent student emails being reused as parent emails."""

    def test_parent_info_form_validation(self):
        form = ParentInfoForm(
            data={"email": "name@email.com"}, student_emails=["name@email.com"]
        )
        self.assertFalse(form.is_valid())
        self.assertIn("email", form.errors)

        form = ParentInfoForm(
            data={
                "first_name": "Pat",
                "last_name": "Parent",
                "relationship_to_student": "parent",
                "email": "name+parent@email.com",
                "address": "123 Main St",
                "city": "Pittsburgh",
                "state": "PA",
                "zip_code": "15213",
                "phone_number": "555-444-1212",
                "phone_type": "cell",
            },
            student_emails=["name@email.com"],
        )
        self.assertTrue(form.is_valid(), form.errors)

        form = ParentInfoForm(
            data={
                "first_name": "Pat",
                "last_name": "Parent",
                "relationship_to_student": "parent",
                "email": "name@email.com",
                "address": "123 Main St",
                "city": "Pittsburgh",
                "state": "PA",
                "zip_code": "15213",
                "phone_number": "555-444-1212",
                "phone_type": "cell",
            },
            student_emails=["name+student@email.com"],
        )
        self.assertTrue(form.is_valid(), form.errors)

        form = ParentInfoForm(
            data={
                "first_name": "Pat",
                "last_name": "Parent",
                "relationship_to_student": "parent",
                "email": "parent@email.com",
                "address": "123 Main St",
                "city": "Pittsburgh",
                "state": "PA",
                "zip_code": "15213",
                "phone_number": "555-444-1212",
                "phone_type": "cell",
            },
            student_emails=["student+something@email.com"],
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_parent_handoff_form_validation(self):
        from applications.forms import ParentHandoffForm

        form = ParentHandoffForm(
            data={"parent_email": "parent@email.com"},
            student_emails=["student+something@email.com"],
        )
        self.assertTrue(form.is_valid(), form.errors)

        form = ParentHandoffForm(
            data={"parent_email": "name+parent@email.com"},
            student_emails=["name@email.com"],
        )
        self.assertTrue(form.is_valid(), form.errors)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class EmailNamesTest(TestCase):
    def setUp(self):
        self.program = Program.objects.create(name="Test Program", active=True)
        self.student_app = Application.objects.create(
            applicant_type=Application.Type.STUDENT,
            email="student@example.com",
            program=self.program,
            status=Application.Status.SUBMITTED,
            data={
                "step5-student": {"legal_first_name": "Jane", "last_name": "Doe"},
                "step7-primaryparent": {"first_name": "John", "last_name": "Doe"},
                "step8-secondaryparent": {"first_name": "Mary", "last_name": "Doe"},
            },
        )
        self.mentor_app = Application.objects.create(
            applicant_type=Application.Type.MENTOR,
            email="mentor@example.com",
            program=self.program,
            status=Application.Status.SUBMITTED,
            data={
                "mentor_info": {"legal_first_name": "James", "last_name": "Smith"},
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
