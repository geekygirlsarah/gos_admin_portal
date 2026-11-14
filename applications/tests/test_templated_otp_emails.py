"""Tests ensuring all OTP code emails are templated and share the same base template."""

from allauth.account.adapter import get_adapter
from django.contrib.auth.models import User
from django.core import mail
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from applications.models import Application
from applications.services import (
    send_application_approved_email,
    send_application_submitted_email,
)
from applications.services import send_otp_email as send_app_otp_email
from applications.services import (
    send_parent_handoff_email,
)
from organizations.models import Organization, OrganizationDomain, OrganizationSettings
from programs.utils.notifications import send_otp_email as send_prog_otp_email


class TemplatedOtpEmailsTests(TestCase):
    def test_application_otp_email_is_templated_with_html_and_text(self):
        app = Application.objects.create(email="applicant@example.com")
        send_app_otp_email(app, "123456")

        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(
            sent.subject, "Your Girls of Steel Robotics application verification code"
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
        self.assertEqual(sent.subject, "Login code for Momentum: 654321")
        self.assertIn("user@example.com", sent.to)
        self.assertIn("654321", sent.body)
        self.assertIn("requested a login code for the Momentum Portal", sent.body)

        # Check HTML message was included
        # send_mail with html_message attaches it to alternatives in EmailMultiAlternatives
        self.assertEqual(len(sent.alternatives), 1)
        html_body, mime = sent.alternatives[0]
        self.assertEqual(mime, "text/html")
        self.assertIn("654321", html_body)
        self.assertIn("requested a login code for the Momentum Portal", html_body)

    def test_allauth_login_code_adapter_rendering(self):
        adapter = get_adapter()
        adapter.send_mail(
            "account/email/login_code", "portaluser@example.com", {"code": "789012"}
        )

        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(sent.subject, "Login code for Momentum: 789012")
        self.assertIn("portaluser@example.com", sent.to)
        self.assertIn("789012", sent.body)
        self.assertIn("requested a login code for the Momentum Portal", sent.body)

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


@override_settings(MOMENTUM_ROOT_DOMAIN="momentumteam.app", EMAIL_ASYNC=False)
class CanonicalApplicationEmailTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            name="Robots", slug="robots", subdomain="robots"
        )
        self.other = Organization.objects.create(
            name="Other", slug="other", subdomain="other"
        )
        OrganizationSettings.objects.update_or_create(
            organization=self.org,
            defaults={
                "data": {
                    "email_settings": {"portal_url": "https://override.example.org"}
                }
            },
        )
        self.domain = OrganizationDomain.objects.create(
            organization=self.org,
            hostname="robots.example.org",
            status=OrganizationDomain.STATUS_ACTIVE,
            dns_verified_at=timezone.now(),
            activated_at=timezone.now(),
            activated_by=User.objects.create_user(username="domain_operator"),
        )
        self.app = Application.objects.create(
            organization=self.org,
            email="applicant@example.com",
            applicant_type=Application.Type.STUDENT,
        )
        self.app.issue_handoff_token()
        self.request = RequestFactory().get("/", HTTP_HOST="other.momentumteam.app")
        self.request.organization = self.other

    def test_application_emails_use_own_active_domain_in_text_and_html(self):
        resume_path = reverse(
            "apply_resume_link", kwargs={"app_id": self.app.application_id}
        )
        handoff_path = reverse(
            "apply_resume_link_with_token",
            kwargs={"app_id": self.app.application_id, "token": self.app.handoff_token},
        )
        for send, path in (
            (lambda: send_app_otp_email(self.app, "123456", self.request), resume_path),
            (
                lambda: send_parent_handoff_email(
                    self.app, "parent@example.com", self.request
                ),
                handoff_path,
            ),
            (
                lambda: send_application_submitted_email(self.app, self.request),
                resume_path,
            ),
            (
                lambda: send_application_approved_email(self.app, self.request),
                resume_path,
            ),
        ):
            with self.subTest(path=path):
                send()
                sent = mail.outbox[-1]
                for body in (sent.body, sent.alternatives[0][0]):
                    self.assertIn("https://robots.example.org" + path, body)
                    self.assertNotIn("other.momentumteam.app", body)
                    self.assertNotIn("override.example.org", body)

    def test_disabled_domain_and_missing_canonical_address(self):
        self.domain.status = OrganizationDomain.STATUS_DISABLED
        self.domain.save(update_fields=["status"])
        send_app_otp_email(self.app, "123456", self.request)
        self.assertIn("https://robots.momentumteam.app", mail.outbox[-1].body)
        with override_settings(MOMENTUM_ROOT_DOMAIN=""):
            send_parent_handoff_email(self.app, "parent@example.com", self.request)
        sent = mail.outbox[-1]
        self.assertNotIn("other.momentumteam.app", sent.body)
        self.assertNotIn('href=""', sent.alternatives[0][0])
        self.assertNotIn(self.app.handoff_token, sent.body)
