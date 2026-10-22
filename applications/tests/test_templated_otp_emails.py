"""Tests ensuring all OTP code emails are templated and share the same base template."""

from allauth.account.adapter import get_adapter
from django.core import mail
from django.template.loader import render_to_string
from django.test import TestCase

from applications.models import Application
from applications.services import send_otp_email as send_app_otp_email
from programs.utils.notifications import send_otp_email as send_prog_otp_email


class TemplatedOtpEmailsTests(TestCase):
    def test_application_otp_email_is_templated_with_html_and_text(self):
        app = Application.objects.create(email="applicant@example.com")
        send_app_otp_email(app, "123456")

        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(
            sent.subject, "Your Girls of Steel application verification code"
        )
        self.assertIn("applicant@example.com", sent.to)
        self.assertIn("123456", sent.body)
        self.assertIn("Thanks for starting an application", sent.body)
        self.assertIn(app.application_id, sent.body)
        self.assertIn("Resume link:", sent.body)

        # Check HTML alternative is attached and contains required info
        self.assertEqual(len(sent.alternatives), 1)
        html_body, mime = sent.alternatives[0]
        self.assertEqual(mime, "text/html")
        self.assertIn("123456", html_body)
        self.assertIn("Thanks for starting an application", html_body)
        self.assertIn(app.application_id, html_body)

    def test_programs_otp_email_is_templated_with_html_and_text(self):
        send_prog_otp_email("user@example.com", "654321")

        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(sent.subject, "Login code for GoS Portal: 654321")
        self.assertIn("user@example.com", sent.to)
        self.assertIn("654321", sent.body)
        self.assertIn("requested a login code for the Girls of Steel Portal", sent.body)

        # Check HTML message was included
        # send_mail with html_message attaches it to alternatives in EmailMultiAlternatives
        self.assertEqual(len(sent.alternatives), 1)
        html_body, mime = sent.alternatives[0]
        self.assertEqual(mime, "text/html")
        self.assertIn("654321", html_body)
        self.assertIn("requested a login code for the Girls of Steel Portal", html_body)

    def test_allauth_login_code_adapter_rendering(self):
        adapter = get_adapter()
        adapter.send_mail(
            "account/email/login_code", "portaluser@example.com", {"code": "789012"}
        )

        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(sent.subject, "Login code for GoS Portal: 789012")
        self.assertIn("portaluser@example.com", sent.to)
        self.assertIn("789012", sent.body)
        self.assertIn("requested a login code for the Girls of Steel Portal", sent.body)

        self.assertEqual(len(sent.alternatives), 1)
        html_body, mime = sent.alternatives[0]
        self.assertEqual(mime, "text/html")
        self.assertIn("789012", html_body)

    def test_template_rendering_custom_subject(self):
        rendered_subject = render_to_string(
            "account/email/login_code_subject.txt",
            {"code": "111222", "subject": "Custom Subject Line"},
        ).strip()
        self.assertEqual(rendered_subject, "Custom Subject Line")
