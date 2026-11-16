import os
from unittest import mock

from allauth.core.context import request_context
from django.contrib.auth.models import AnonymousUser, User
from django.core import mail
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from GoSAdminPortal.adapter import AccountAdapter
from organizations.models import Organization, OrganizationDomain, OrganizationSettings


class AccountAdapterLoggingTests(TestCase):
    @override_settings(DEBUG=False)
    def test_print_login_code_always_logs_for_unknown_account(self):
        adapter = AccountAdapter()
        template = "account/email/unknown_account"
        email = "unknown@example.com"
        context = {}

        with mock.patch.dict(os.environ, {"PRINT_LOGIN_CODE_ALWAYS": "1"}, clear=False):
            with self.assertLogs(level="INFO") as cm:
                adapter.send_mail(template, email, context)

        joined = "\n".join(cm.output)
        self.assertIn("PRINT_LOGIN_CODE_ALWAYS", joined)
        self.assertIn(email, joined)
        self.assertIn("(none)", joined)

    @override_settings(DEBUG=False)
    def test_print_login_code_always_logs_with_code_any_template(self):
        adapter = AccountAdapter()
        template = "account/email/login_code"
        email = "student@example.com"
        context = {"code": "654321"}

        with mock.patch.dict(
            os.environ, {"PRINT_LOGIN_CODE_ALWAYS": "true"}, clear=False
        ):
            with self.assertLogs(level="INFO") as cm:
                adapter.send_mail(template, email, context)

        joined = "\n".join(cm.output)
        self.assertIn("PRINT_LOGIN_CODE_ALWAYS", joined)
        self.assertIn(email, joined)
        self.assertIn("654321", joined)


@override_settings(MOMENTUM_ROOT_DOMAIN="momentumteam.app", EMAIL_ASYNC=False)
class AccountAdapterOrganizationTests(TestCase):
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
                "data": {"email_settings": {"portal_url": "https://wrong.example.org"}}
            },
        )
        self.request = RequestFactory().get(
            "/accounts/login/", HTTP_HOST="other.momentumteam.app"
        )
        self.request.organization = self.org
        self.request.user = AnonymousUser()

    def test_login_email_uses_request_tenant_not_request_host_or_override(self):
        with request_context(self.request):
            AccountAdapter().send_mail(
                "account/email/login_code", "user@example.com", {"code": "123456"}
            )
        html = mail.outbox[0].alternatives[0][0]
        self.assertIn('href="https://robots.momentumteam.app"', html)
        self.assertNotIn("other.momentumteam.app", html)
        self.assertNotIn("wrong.example.org", html)

    def test_explicit_organization_takes_precedence_for_branding(self):
        with request_context(self.request):
            AccountAdapter().send_mail(
                "account/email/login_code",
                "user@example.com",
                {"code": "123456", "organization": self.other},
            )
        html = mail.outbox[0].alternatives[0][0]
        self.assertIn('href="https://other.momentumteam.app"', html)
        self.assertNotIn('href="https://robots.momentumteam.app"', html)

    def test_current_request_takes_precedence_over_stale_adapter_request(self):
        other_request = RequestFactory().get("/accounts/login/")
        other_request.organization = self.other
        with request_context(other_request):
            adapter = AccountAdapter()
        with request_context(self.request):
            adapter.send_mail(
                "account/email/login_code", "user@example.com", {"code": "123456"}
            )
        html = mail.outbox[0].alternatives[0][0]
        self.assertIn('href="https://robots.momentumteam.app"', html)
        self.assertNotIn("other.momentumteam.app", html)

    def test_direct_render_uses_request_tenant(self):
        with request_context(self.request):
            message = AccountAdapter().render_mail(
                "account/email/login_code", "user@example.com", {"code": "123456"}
            )
        html = message.alternatives[0][0]
        self.assertIn('href="https://robots.momentumteam.app"', html)
        self.assertNotIn("other.momentumteam.app", html)

    def test_login_email_uses_active_domain_and_falls_back_when_disabled(self):
        domain = OrganizationDomain.objects.create(
            organization=self.org,
            hostname="robots.example.org",
            status=OrganizationDomain.STATUS_ACTIVE,
            dns_verified_at=timezone.now(),
            activated_at=timezone.now(),
            activated_by=User.objects.create_user(username="domain_operator"),
        )
        with request_context(self.request):
            AccountAdapter().send_mail(
                "account/email/login_code", "user@example.com", {"code": "123456"}
            )
        self.assertIn(
            'href="https://robots.example.org"', mail.outbox[-1].alternatives[0][0]
        )
        domain.status = OrganizationDomain.STATUS_DISABLED
        domain.save(update_fields=["status"])
        with request_context(self.request):
            AccountAdapter().send_mail(
                "account/email/login_code", "user@example.com", {"code": "123456"}
            )
        self.assertIn(
            'href="https://robots.momentumteam.app"', mail.outbox[-1].alternatives[0][0]
        )
        self.assertNotIn("robots.example.org", mail.outbox[-1].alternatives[0][0])

    def test_login_email_without_safe_tenant_url_does_not_use_default(self):
        self.org.is_active = False
        self.org.save(update_fields=["is_active"])
        with request_context(self.request):
            AccountAdapter().send_mail(
                "account/email/login_code", "user@example.com", {"code": "123456"}
            )
        html = mail.outbox[0].alternatives[0][0]
        self.assertIn("Robots Portal", html)
        self.assertNotIn('href="https://', html)
        self.assertNotIn("gos.momentumteam.app", html)

    @override_settings(
        EMAIL_ASYNC=True,
        MAILERS={
            "default": {"BACKEND": "django.core.mail.backends.dummy.EmailBackend"}
        },
    )
    def test_background_delivery_captures_tenant_before_thread_starts(self):
        adapter = AccountAdapter()
        with mock.patch("GoSAdminPortal.adapter.threading.Thread") as thread:
            with request_context(self.request):
                adapter.send_mail(
                    "account/email/login_code", "user@example.com", {"code": "123456"}
                )
            kwargs = thread.call_args.kwargs
            with (
                override_settings(
                    MAILERS={
                        "default": {
                            "BACKEND": "django.core.mail.backends.locmem.EmailBackend"
                        }
                    }
                ),
                mock.patch("django.db.close_old_connections"),
            ):
                kwargs["target"](**kwargs["kwargs"])
        html = mail.outbox[0].alternatives[0][0]
        self.assertIn('href="https://robots.momentumteam.app"', html)
