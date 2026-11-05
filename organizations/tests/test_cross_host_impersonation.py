from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from audit.events import AuditEvent
from audit.models import AuditLog
from organizations.models import (
    Organization,
    OrganizationDomain,
    SupportImpersonationHandoff,
    UserOrganizationMembership,
)


@override_settings(
    ALLOWED_HOSTS=["*"],
    MOMENTUM_ROOT_DOMAIN="momentumteam.app",
    SITE_URL="https://momentumteam.app",
)
class CrossHostImpersonationTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.platform_admin = user_model.objects.create_superuser(
            username="support-admin", email="support@example.com"
        )
        self.target_user = user_model.objects.create_user(
            username="tenant-user", email="tenant@example.com"
        )
        self.organization = Organization.objects.create(
            name="Alpha Team", slug="alpha", subdomain="alpha"
        )
        UserOrganizationMembership.objects.create(
            user=self.target_user,
            organization=self.organization,
            role="Mentor",
            is_active=True,
        )
        self.platform_client = Client()
        self.platform_client.force_login(self.platform_admin)

    def test_starting_support_impersonation_keeps_platform_session_admin(self):
        response = self.start_handoff()

        self.assertEqual(response.status_code, 200)
        self.assertIn("alpha.momentumteam.app", response.context["tenant_post_url"])
        self.assertNotIn(
            response.context["handoff_token"], response.context["tenant_post_url"]
        )
        self.assertNotIn(
            str(response.context["handoff_id"]), response.context["tenant_post_url"]
        )
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(
            int(self.platform_client.session["_auth_user_id"]),
            self.platform_admin.pk,
        )
        handoff = SupportImpersonationHandoff.objects.get(
            pk=response.context["handoff_id"]
        )
        self.assertEqual(handoff.initiated_by, self.platform_admin)
        self.assertTrue(handoff.initiator_session_hash)

    def test_handoff_lifecycle_uses_independent_host_sessions(self):
        response = self.start_handoff()
        tenant_client = Client()

        redeem_response = self.redeem(response, tenant_client)
        self.assertRedirects(
            redeem_response,
            reverse("organizations:support_impersonation_confirm"),
            fetch_redirect_response=False,
        )
        self.assertNotIn("_auth_user_id", tenant_client.session)

        confirmation = tenant_client.get(
            reverse("organizations:support_impersonation_confirm"),
            HTTP_HOST="alpha.momentumteam.app",
        )
        self.assertEqual(confirmation.status_code, 200)
        self.assertContains(confirmation, "Confirm support access")
        self.assertNotIn("_auth_user_id", tenant_client.session)

        confirm_response = tenant_client.post(
            reverse("organizations:support_impersonation_confirm"),
            HTTP_HOST="alpha.momentumteam.app",
        )
        self.assertRedirects(
            confirm_response,
            reverse("profile_dashboard"),
            fetch_redirect_response=False,
        )
        dashboard = tenant_client.get(
            reverse("profile_dashboard"), HTTP_HOST="alpha.momentumteam.app"
        )
        self.assertEqual(dashboard.wsgi_request.user, self.target_user)
        self.assertTrue(dashboard.context["is_impersonating"])
        self.assertEqual(
            int(self.platform_client.session["_auth_user_id"]), self.platform_admin.pk
        )
        self.assertTrue(
            AuditLog.objects.filter(
                event=AuditEvent.USER_IMPERSONATED, actor=self.platform_admin
            ).exists()
        )
        self.membership().delete()
        self.assertEqual(
            tenant_client.get(
                reverse("profile_dashboard"), HTTP_HOST="alpha.momentumteam.app"
            ).status_code,
            403,
        )

        stop_response = tenant_client.post(
            reverse("platform_admin:impersonate_stop"),
            HTTP_HOST="alpha.momentumteam.app",
        )
        self.assertRedirects(
            stop_response,
            f"https://momentumteam.app{reverse('platform_admin:dashboard')}",
            fetch_redirect_response=False,
        )
        self.assertNotIn("_auth_user_id", tenant_client.session)
        self.assertEqual(
            int(self.platform_client.session["_auth_user_id"]), self.platform_admin.pk
        )
        self.assertTrue(
            AuditLog.objects.filter(
                event=AuditEvent.IMPERSONATION_ENDED, actor=self.platform_admin
            ).exists()
        )

    def test_selected_membership_controls_destination(self):
        second = Organization.objects.create(
            name="Beta Team", slug="beta", subdomain="beta"
        )
        UserOrganizationMembership.objects.create(
            user=self.target_user, organization=second, role="Parent", is_active=True
        )

        response = self.start_handoff(organization=second)

        self.assertEqual(response.status_code, 200)
        self.assertIn("beta.momentumteam.app", response.context["tenant_post_url"])
        self.assertEqual(
            SupportImpersonationHandoff.objects.get(
                pk=response.context["handoff_id"]
            ).organization,
            second,
        )

    def test_support_directory_offers_one_button_per_active_organization(self):
        UserOrganizationMembership.objects.create(
            user=self.target_user,
            organization=self.organization,
            role="Parent",
            is_active=True,
        )

        response = self.platform_client.get(
            reverse("platform_admin:user_support"), HTTP_HOST="momentumteam.app"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.count(b'name="organization_id"'), 1)
        self.assertContains(response, "Alpha Team")

    def test_platform_handoff_creation_requires_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.platform_admin)

        response = client.post(
            reverse(
                "platform_admin:impersonate_user",
                kwargs={"pk": self.target_user.pk},
            ),
            {"organization_id": self.organization.pk},
            HTTP_HOST="momentumteam.app",
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(SupportImpersonationHandoff.objects.exists())

    def test_only_platform_superusers_can_start_on_the_root_host(self):
        regular_user = get_user_model().objects.create_user(username="regular-user")
        regular_client = Client()
        regular_client.force_login(regular_user)
        start_url = reverse(
            "platform_admin:impersonate_user",
            kwargs={"pk": self.target_user.pk},
        )

        denied = regular_client.post(
            start_url,
            {"organization_id": self.organization.pk},
            HTTP_HOST="momentumteam.app",
        )
        wrong_host = self.platform_client.post(
            start_url,
            {"organization_id": self.organization.pk},
            HTTP_HOST="alpha.momentumteam.app",
        )

        self.assertEqual(denied.status_code, 403)
        self.assertRedirects(wrong_host, reverse("home"), fetch_redirect_response=False)
        self.assertFalse(SupportImpersonationHandoff.objects.exists())

    def test_missing_or_inactive_membership_cannot_start_handoff(self):
        self.membership().delete()
        response = self.start_handoff()
        self.assertEqual(response.status_code, 302)
        self.assertFalse(SupportImpersonationHandoff.objects.exists())

        UserOrganizationMembership.objects.create(
            user=self.target_user,
            organization=self.organization,
            role="Mentor",
            is_active=False,
        )
        response = self.start_handoff()
        self.assertEqual(response.status_code, 302)
        self.assertFalse(SupportImpersonationHandoff.objects.exists())

    def test_wrong_host_does_not_consume_handoff_and_replay_is_rejected(self):
        response = self.start_handoff()
        token_data = self.handoff_data(response)
        beta = Organization.objects.create(name="Beta", slug="beta", subdomain="beta")
        UserOrganizationMembership.objects.create(
            user=self.target_user, organization=beta, role="Mentor", is_active=True
        )
        tenant_client = Client()
        wrong_host = tenant_client.post(
            reverse("organizations:support_impersonation_handoff"),
            token_data,
            HTTP_HOST="beta.momentumteam.app",
        )
        self.assertEqual(wrong_host.status_code, 404)

        correct_host = self.redeem(response, tenant_client)
        self.assertEqual(correct_host.status_code, 302)
        replay = Client().post(
            reverse("organizations:support_impersonation_handoff"),
            token_data,
            HTTP_HOST="alpha.momentumteam.app",
        )
        self.assertEqual(replay.status_code, 404)

    def test_stolen_handoff_cannot_authenticate_a_different_browser(self):
        response = self.start_handoff()
        other_browser = Client()
        prepared = self.prepare_handoff(response, other_browser)
        other_browser.post(
            reverse("organizations:support_impersonation_redeem"),
            self.prepared_handoff_data(prepared),
            HTTP_HOST="alpha.momentumteam.app",
        )

        other_browser.post(
            reverse("organizations:support_impersonation_confirm"),
            HTTP_HOST="alpha.momentumteam.app",
        )

        self.assertNotIn("_auth_user_id", other_browser.session)

    def test_platform_confirmation_requires_the_initiating_session(self):
        response = self.start_handoff()
        tenant_client = Client()
        prepared = self.prepare_handoff(response, tenant_client)
        tenant_client.post(
            reverse("organizations:support_impersonation_redeem"),
            self.prepared_handoff_data(prepared),
            HTTP_HOST="alpha.momentumteam.app",
        )
        authorize_url = reverse(
            "platform_admin:impersonation_authorize",
            kwargs={"handoff_id": prepared.context["handoff_id"]},
        )
        different_session = Client()
        different_session.force_login(self.platform_admin)

        self.assertEqual(
            different_session.get(
                authorize_url, HTTP_HOST="momentumteam.app"
            ).status_code,
            404,
        )
        self.assertEqual(
            self.platform_client.get(
                authorize_url, HTTP_HOST="momentumteam.app"
            ).status_code,
            200,
        )

    def test_platform_and_tenant_return_steps_require_csrf_and_matching_session(self):
        response = self.start_handoff()
        tenant_client = Client(enforce_csrf_checks=True)
        prepared = self.prepare_handoff(response, tenant_client)
        tenant_stage = tenant_client.post(
            reverse("organizations:support_impersonation_redeem"),
            self.prepared_handoff_data(prepared),
            HTTP_HOST="alpha.momentumteam.app",
        )
        self.assertEqual(tenant_stage.status_code, 200)
        authorize_url = reverse(
            "platform_admin:impersonation_authorize",
            kwargs={"handoff_id": prepared.context["handoff_id"]},
        )
        platform_client = Client(enforce_csrf_checks=True)
        platform_client.cookies[settings.SESSION_COOKIE_NAME] = (
            self.platform_client.cookies[settings.SESSION_COOKIE_NAME].value
        )
        self.assertEqual(
            platform_client.get(
                authorize_url, HTTP_HOST="momentumteam.app"
            ).status_code,
            200,
        )
        self.assertEqual(
            platform_client.post(
                authorize_url, HTTP_HOST="momentumteam.app"
            ).status_code,
            403,
        )
        platform_return = platform_client.post(
            authorize_url,
            HTTP_HOST="momentumteam.app",
            HTTP_X_CSRFTOKEN=platform_client.cookies[settings.CSRF_COOKIE_NAME].value,
        )
        self.assertEqual(platform_return.status_code, 200)
        return_data = {
            "stage": "platform_return",
            "handoff_id": str(prepared.context["handoff_id"]),
            "handoff_token": platform_return.context["handoff_token"],
        }
        other_tenant = Client()
        wrong_browser_page = other_tenant.post(
            reverse("organizations:support_impersonation_handoff"),
            return_data,
            HTTP_HOST="alpha.momentumteam.app",
        )
        self.assertEqual(wrong_browser_page.status_code, 200)
        self.assertEqual(
            other_tenant.post(
                reverse("organizations:support_impersonation_resume"),
                self.prepared_handoff_data(wrong_browser_page),
                HTTP_HOST="alpha.momentumteam.app",
            ).status_code,
            404,
        )
        return_page = tenant_client.post(
            reverse("organizations:support_impersonation_handoff"),
            return_data,
            HTTP_HOST="alpha.momentumteam.app",
        )
        resume_url = reverse("organizations:support_impersonation_resume")
        resume_data = self.prepared_handoff_data(return_page)
        self.assertEqual(
            tenant_client.post(
                resume_url,
                {
                    key: value
                    for key, value in resume_data.items()
                    if key != "csrfmiddlewaretoken"
                },
                HTTP_HOST="alpha.momentumteam.app",
            ).status_code,
            403,
        )
        self.assertEqual(
            tenant_client.post(
                resume_url, resume_data, HTTP_HOST="alpha.momentumteam.app"
            ).status_code,
            302,
        )
        handoff = SupportImpersonationHandoff.objects.get(
            pk=prepared.context["handoff_id"]
        )
        self.assertEqual(handoff.platform_token_hash, "")
        self.assertIsNotNone(handoff.platform_returned_at)
        self.assertEqual(
            tenant_client.post(
                resume_url, resume_data, HTTP_HOST="alpha.momentumteam.app"
            ).status_code,
            404,
        )

    def test_locked_handoff_queries_do_not_join_nullable_relations(self):
        response = self.start_handoff()
        tenant_client = Client()
        prepared = self.prepare_handoff(response, tenant_client)
        with CaptureQueriesContext(connection) as queries:
            tenant_client.post(
                reverse("organizations:support_impersonation_redeem"),
                self.prepared_handoff_data(prepared),
                HTTP_HOST="alpha.momentumteam.app",
            )
        self.assert_handoff_selects_have_no_joins(queries)

        authorize_url = reverse(
            "platform_admin:impersonation_authorize",
            kwargs={"handoff_id": prepared.context["handoff_id"]},
        )
        with CaptureQueriesContext(connection) as queries:
            platform_return = self.platform_client.post(
                authorize_url, HTTP_HOST="momentumteam.app"
            )
        self.assert_handoff_selects_have_no_joins(queries)

        return_prepared = tenant_client.post(
            reverse("organizations:support_impersonation_handoff"),
            {
                "stage": "platform_return",
                "handoff_id": str(prepared.context["handoff_id"]),
                "handoff_token": platform_return.context["handoff_token"],
            },
            HTTP_HOST="alpha.momentumteam.app",
        )
        with CaptureQueriesContext(connection) as queries:
            tenant_client.post(
                reverse("organizations:support_impersonation_resume"),
                self.prepared_handoff_data(return_prepared),
                HTTP_HOST="alpha.momentumteam.app",
            )
        self.assert_handoff_selects_have_no_joins(queries)

        with CaptureQueriesContext(connection) as queries:
            tenant_client.post(
                reverse("organizations:support_impersonation_confirm"),
                HTTP_HOST="alpha.momentumteam.app",
            )
        self.assert_handoff_selects_have_no_joins(queries)

        with CaptureQueriesContext(connection) as queries:
            tenant_client.post(
                reverse("platform_admin:impersonate_stop"),
                HTTP_HOST="alpha.momentumteam.app",
            )
        self.assert_handoff_selects_have_no_joins(queries)

    def test_new_handoff_replaces_and_closes_an_abandoned_pending_handoff(self):
        first_response = self.start_handoff()
        first = SupportImpersonationHandoff.objects.get(
            pk=first_response.context["handoff_id"]
        )
        tenant_client = Client()
        self.assertEqual(self.redeem(first_response, tenant_client).status_code, 302)

        second_response = self.start_handoff()
        self.assertEqual(self.redeem(second_response, tenant_client).status_code, 302)
        first.refresh_from_db()
        self.assertIsNotNone(first.ended_at)

        confirmation = tenant_client.get(
            reverse("organizations:support_impersonation_confirm"),
            HTTP_HOST="alpha.momentumteam.app",
        )
        self.assertEqual(confirmation.status_code, 200)
        self.assertContains(confirmation, "Confirm support access")

    def test_revoked_membership_prevents_confirmation(self):
        response = self.start_handoff()
        tenant_client = Client()
        self.redeem(response, tenant_client)
        self.membership().delete()

        confirmation = tenant_client.get(
            reverse("organizations:support_impersonation_confirm"),
            HTTP_HOST="alpha.momentumteam.app",
        )

        self.assertEqual(confirmation.status_code, 404)
        self.assertNotIn("_auth_user_id", tenant_client.session)
        self.assertFalse(
            AuditLog.objects.filter(event=AuditEvent.USER_IMPERSONATED).exists()
        )

    def test_expired_or_deactivated_initiator_cannot_redeem(self):
        expired_response = self.start_handoff()
        expired = SupportImpersonationHandoff.objects.get(
            pk=expired_response.context["handoff_id"]
        )
        expired.expires_at = timezone.now() - timedelta(seconds=1)
        expired.save(update_fields=["expires_at"])
        self.assertEqual(self.redeem(expired_response, Client()).status_code, 404)

        inactive_target_response = self.start_handoff()
        self.target_user.is_active = False
        self.target_user.save(update_fields=["is_active"])
        self.assertEqual(
            self.redeem(inactive_target_response, Client()).status_code, 404
        )
        self.target_user.is_active = True
        self.target_user.save(update_fields=["is_active"])

        response = self.start_handoff()
        self.platform_admin.is_active = False
        self.platform_admin.save(update_fields=["is_active"])
        self.assertEqual(self.redeem(response, Client()).status_code, 404)

    def test_existing_tenant_login_is_only_replaced_after_confirmation(self):
        current_user = get_user_model().objects.create_user(username="current-user")
        UserOrganizationMembership.objects.create(
            user=current_user, organization=self.organization, role="Parent"
        )
        response = self.start_handoff()
        tenant_client = Client()
        tenant_client.force_login(current_user)

        prepared = self.prepare_handoff(response, tenant_client)
        self.assertEqual(prepared.status_code, 200)
        self.assertEqual(prepared["Cache-Control"], "no-store")
        self.assertEqual(int(tenant_client.session["_auth_user_id"]), current_user.pk)
        self.assertNotIn(settings.SESSION_COOKIE_NAME, prepared.cookies)
        self.complete_handoff(prepared, tenant_client)
        confirmation = tenant_client.get(
            reverse("organizations:support_impersonation_confirm"),
            HTTP_HOST="alpha.momentumteam.app",
        )
        self.assertContains(confirmation, "currently signed in here as")
        self.assertEqual(int(tenant_client.session["_auth_user_id"]), current_user.pk)

        tenant_client.post(
            reverse("organizations:support_impersonation_confirm"),
            HTTP_HOST="alpha.momentumteam.app",
        )
        self.assertEqual(
            int(tenant_client.session["_auth_user_id"]), self.target_user.pk
        )

    @override_settings(
        ALLOWED_HOSTS=["momentumteam.app", ".momentumteam.app", "portal.example.org"]
    )
    def test_custom_domain_preserves_existing_login_until_confirmation(self):
        OrganizationDomain.objects.create(
            organization=self.organization,
            hostname="portal.example.org",
            status=OrganizationDomain.STATUS_ACTIVE,
            dns_verified_at=timezone.now(),
            activated_at=timezone.now(),
            activated_by=self.platform_admin,
        )
        current_user = get_user_model().objects.create_user(username="custom-user")
        UserOrganizationMembership.objects.create(
            user=current_user, organization=self.organization, role="Parent"
        )
        response = self.start_handoff()
        self.assertIn("portal.example.org", response.context["tenant_post_url"])
        tenant_client = Client()
        tenant_client.force_login(current_user)

        prepared = self.prepare_handoff(
            response, tenant_client, host="portal.example.org"
        )
        self.assertEqual(prepared.status_code, 200)
        self.assertNotIn(settings.SESSION_COOKIE_NAME, prepared.cookies)
        self.assertEqual(
            self.complete_handoff(
                prepared, tenant_client, host="portal.example.org"
            ).status_code,
            302,
        )
        confirmation = tenant_client.get(
            reverse("organizations:support_impersonation_confirm"),
            HTTP_HOST="portal.example.org",
        )
        self.assertContains(confirmation, "currently signed in here as")
        self.assertEqual(int(tenant_client.session["_auth_user_id"]), current_user.pk)
        tenant_client.post(
            reverse("organizations:support_impersonation_confirm"),
            HTTP_HOST="portal.example.org",
        )
        self.assertEqual(
            int(tenant_client.session["_auth_user_id"]), self.target_user.pk
        )

    @override_settings(MENTOR_AGREEMENT_ENABLED=True)
    def test_handoff_pages_do_not_bypass_mentor_agreement_after_login(self):
        current_user = get_user_model().objects.create_user(username="current-mentor")
        UserOrganizationMembership.objects.create(
            user=current_user, organization=self.organization, role="Mentor"
        )
        response = self.start_handoff()
        tenant_client = Client()
        tenant_client.force_login(current_user)

        with (
            patch("programs.permission_views.user_is_mentor", return_value=True),
            patch(
                "programs.models.MentorAgreementAcceptance.has_accepted_for_user",
                return_value=False,
            ),
        ):
            self.assertEqual(self.redeem(response, tenant_client).status_code, 302)
            confirmation = tenant_client.get(
                reverse("organizations:support_impersonation_confirm"),
                HTTP_HOST="alpha.momentumteam.app",
            )
            self.assertEqual(confirmation.status_code, 200)
            tenant_client.post(
                reverse("organizations:support_impersonation_confirm"),
                HTTP_HOST="alpha.momentumteam.app",
            )
            dashboard = tenant_client.get(
                reverse("profile_dashboard"), HTTP_HOST="alpha.momentumteam.app"
            )

        self.assertEqual(dashboard.status_code, 302)
        self.assertTrue(dashboard.url.startswith(reverse("mentor_agreement")))

    def test_unknown_handoff_path_is_not_public(self):
        response = Client().get(
            "/organizations/support-impersonation/unrecognized/",
            HTTP_HOST="alpha.momentumteam.app",
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(settings.LOGIN_URL))

    def test_exit_banner_survives_originating_admin_demotion(self):
        response = self.start_handoff()
        tenant_client = Client()
        self.redeem(response, tenant_client)
        tenant_client.post(
            reverse("organizations:support_impersonation_confirm"),
            HTTP_HOST="alpha.momentumteam.app",
        )

        self.platform_admin.is_superuser = False
        self.platform_admin.save(update_fields=["is_superuser"])
        dashboard = tenant_client.get(
            reverse("profile_dashboard"), HTTP_HOST="alpha.momentumteam.app"
        )

        self.assertTrue(dashboard.context["is_impersonating"])
        self.assertContains(dashboard, "Exit Impersonation")
        stopped = tenant_client.post(
            reverse("platform_admin:impersonate_stop"),
            HTTP_HOST="alpha.momentumteam.app",
        )
        self.assertEqual(stopped.status_code, 302)
        self.assertNotIn("_auth_user_id", tenant_client.session)

    def test_confirmation_requires_csrf_and_audit_success(self):
        response = self.start_handoff()
        tenant_client = Client(enforce_csrf_checks=True)
        prepared = self.prepare_handoff(response, tenant_client)
        self.assertEqual(prepared.status_code, 200)
        redeem_url = reverse("organizations:support_impersonation_redeem")
        data = self.prepared_handoff_data(prepared)
        invalid_data = {
            key: value for key, value in data.items() if key != "csrfmiddlewaretoken"
        }
        self.assertEqual(
            tenant_client.post(
                redeem_url, invalid_data, HTTP_HOST="alpha.momentumteam.app"
            ).status_code,
            403,
        )
        self.complete_handoff(prepared, tenant_client)
        confirm_url = reverse("organizations:support_impersonation_confirm")
        confirmation = tenant_client.get(
            confirm_url, HTTP_HOST="alpha.momentumteam.app"
        )
        self.assertEqual(confirmation.status_code, 200)
        self.assertEqual(
            tenant_client.post(
                confirm_url, HTTP_HOST="alpha.momentumteam.app"
            ).status_code,
            403,
        )
        csrf_token = tenant_client.cookies["csrftoken"].value
        with patch("organizations.platform_views.log_event", return_value=None):
            failed_audit = tenant_client.post(
                confirm_url,
                HTTP_HOST="alpha.momentumteam.app",
                HTTP_X_CSRFTOKEN=csrf_token,
            )
        self.assertEqual(failed_audit.status_code, 503)
        self.assertNotIn("_auth_user_id", tenant_client.session)

        successful = tenant_client.post(
            confirm_url,
            HTTP_HOST="alpha.momentumteam.app",
            HTTP_X_CSRFTOKEN=csrf_token,
        )
        self.assertEqual(successful.status_code, 302)
        self.assertEqual(
            int(tenant_client.session["_auth_user_id"]), self.target_user.pk
        )

    def membership(self, is_active=True):
        return UserOrganizationMembership.objects.get(
            user=self.target_user,
            organization=self.organization,
            is_active=is_active,
        )

    def assert_handoff_selects_have_no_joins(self, queries):
        selects = [
            query["sql"]
            for query in queries
            if query["sql"].lstrip().upper().startswith("SELECT")
            and '"organizations_supportimpersonationhandoff"' in query["sql"]
        ]
        self.assertTrue(selects)
        for sql in selects:
            self.assertNotIn(" JOIN ", sql.upper())

    def start_handoff(self, organization=None):
        return self.platform_client.post(
            reverse(
                "platform_admin:impersonate_user",
                kwargs={"pk": self.target_user.pk},
            ),
            {"organization_id": (organization or self.organization).pk},
            HTTP_HOST="momentumteam.app",
        )

    @staticmethod
    def handoff_data(response):
        return {
            "handoff_id": str(response.context["handoff_id"]),
            "handoff_token": response.context["handoff_token"],
        }

    def redeem(self, response, tenant_client, host="alpha.momentumteam.app"):
        prepared = self.prepare_handoff(response, tenant_client, host=host)
        if prepared.status_code != 200:
            return prepared
        return self.complete_handoff(prepared, tenant_client, host=host)

    def prepare_handoff(self, response, tenant_client, host="alpha.momentumteam.app"):
        return tenant_client.post(
            reverse("organizations:support_impersonation_handoff"),
            self.handoff_data(response),
            HTTP_HOST=host,
        )

    def complete_handoff(self, prepared, tenant_client, host="alpha.momentumteam.app"):
        tenant_stage = tenant_client.post(
            reverse("organizations:support_impersonation_redeem"),
            self.prepared_handoff_data(prepared),
            HTTP_HOST=host,
        )
        if tenant_stage.status_code != 200:
            return tenant_stage
        authorize_url = reverse(
            "platform_admin:impersonation_authorize",
            kwargs={"handoff_id": prepared.context["handoff_id"]},
        )
        platform_page = self.platform_client.get(
            authorize_url, HTTP_HOST="momentumteam.app"
        )
        if platform_page.status_code != 200:
            return platform_page
        platform_return = self.platform_client.post(
            authorize_url, HTTP_HOST="momentumteam.app"
        )
        if platform_return.status_code != 200:
            return platform_return
        return_prepared = tenant_client.post(
            reverse("organizations:support_impersonation_handoff"),
            {
                "stage": "platform_return",
                "handoff_id": str(prepared.context["handoff_id"]),
                "handoff_token": platform_return.context["handoff_token"],
            },
            HTTP_HOST=host,
        )
        if return_prepared.status_code != 200:
            return return_prepared
        return tenant_client.post(
            reverse("organizations:support_impersonation_resume"),
            self.prepared_handoff_data(return_prepared),
            HTTP_HOST=host,
        )

    @staticmethod
    def prepared_handoff_data(response):
        return {
            "handoff_id": str(response.context["handoff_id"]),
            "handoff_token": response.context["handoff_token"],
            "csrfmiddlewaretoken": str(response.context["csrf_token"]),
        }
