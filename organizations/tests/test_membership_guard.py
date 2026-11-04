from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils.functional import SimpleLazyObject

from audit.events import AuditEvent
from audit.models import AuditLog
from organizations.models import (
    Organization,
    OrganizationInvitation,
    UserOrganizationMembership,
)


@override_settings(MOMENTUM_ROOT_DOMAIN="momentumteam.app", ALLOWED_HOSTS=["*"])
class TenantMembershipGuardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.organization = Organization.objects.create(
            name="Guard Team", slug="guard-team", subdomain="guard-team"
        )
        cls.other = Organization.objects.create(
            name="Other Team", slug="other-team", subdomain="other-team"
        )
        cls.user = get_user_model().objects.create_user(
            username="guard-user", email="guard@example.com"
        )
        cls.host = "guard-team.momentumteam.app"

    def membership(self, organization=None, role="Student", is_active=True):
        return UserOrganizationMembership.objects.create(
            user=self.user,
            organization=organization or self.organization,
            role=role,
            is_active=is_active,
        )

    def tenant_get(self, path="/"):
        return self.client.get(path, HTTP_HOST=self.host)

    def test_active_membership_allows_each_role(self):
        self.client.force_login(self.user)
        for role, _ in UserOrganizationMembership.ROLE_CHOICES:
            with self.subTest(role=role):
                membership = self.membership(role=role)
                self.assertRedirects(
                    self.tenant_get(),
                    reverse("profile_dashboard"),
                    fetch_redirect_response=False,
                )
                membership.delete()

    def test_missing_inactive_and_cross_organization_memberships_denied(self):
        self.client.force_login(self.user)
        self.assertEqual(self.tenant_get().status_code, 403)
        self.membership(is_active=False)
        self.assertEqual(self.tenant_get().status_code, 403)
        self.membership(organization=self.other)
        self.assertEqual(self.tenant_get().status_code, 403)
        self.assertEqual(
            self.client.get("/", HTTP_HOST="other-team.momentumteam.app").status_code,
            302,
        )

    def test_group_only_lead_mentor_cannot_enter_tenant(self):
        self.user.groups.add(Group.objects.get_or_create(name="LeadMentor")[0])
        self.client.force_login(self.user)
        self.assertEqual(self.tenant_get().status_code, 403)

    def test_any_active_role_allows_access_and_revocation_takes_effect(self):
        self.membership(role="Mentor", is_active=False)
        active = self.membership(role="Parent")
        self.client.force_login(self.user)
        self.assertEqual(self.tenant_get().status_code, 302)
        active.is_active = False
        active.save(update_fields=["is_active"])
        self.assertEqual(self.tenant_get().status_code, 403)

    def test_superuser_can_enter_without_membership(self):
        admin = get_user_model().objects.create_superuser(username="guard-admin")
        self.client.force_login(admin)
        self.assertEqual(self.tenant_get().status_code, 302)

    def test_nonmember_can_end_an_audited_impersonation_session(self):
        admin = get_user_model().objects.create_superuser(username="guard-admin")
        self.client.force_login(self.user)
        session = self.client.session
        session["impersonator_id"] = admin.pk
        session.save()
        self.assertEqual(self.tenant_get().status_code, 403)
        response = self.client.post(
            reverse("platform_admin:impersonate_stop"), HTTP_HOST=self.host
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(int(self.client.session["_auth_user_id"]), admin.pk)
        self.assertTrue(
            AuditLog.objects.filter(
                event=AuditEvent.IMPERSONATION_ENDED, actor=admin
            ).exists()
        )

    def test_mixed_public_private_prefixes_and_unrelated_account_paths_denied(self):
        self.client.force_login(self.user)
        for path in (
            "/apply/",
            "/apply/review/",
            "/calendar/feeds/",
            "/calendar/api/events/",
            "/guest/review/",
            "/accounts/email/",
            "/organizations/invitations/extra/path/",
            "/platform-admin/",
            "/mentor-agreement/",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.tenant_get(path).status_code, 403)

    def test_anonymous_login_signup_and_public_application_remain_reachable(self):
        for path in ("/accounts/login/", "/accounts/signup/", "/apply/"):
            with self.subTest(path=path):
                self.assertNotEqual(self.tenant_get(path).status_code, 403)
        response = self.tenant_get("/programs/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    def test_nonmember_can_reach_authentication_and_accept_invitation(self):
        invitation = OrganizationInvitation.objects.create(
            organization=self.organization, email=self.user.email, role="Student"
        )
        path = reverse("organizations:accept_invitation", args=[invitation.token])
        response = self.tenant_get(path)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

        self.client.force_login(self.user)
        for auth_path in ("/accounts/login/", "/accounts/logout/", "/accounts/signup/"):
            with self.subTest(path=auth_path):
                self.assertNotEqual(self.tenant_get(auth_path).status_code, 403)
        self.assertEqual(self.tenant_get(path).status_code, 200)
        self.assertEqual(self.client.post(path, HTTP_HOST=self.host).status_code, 302)
        self.assertTrue(
            UserOrganizationMembership.objects.filter(
                user=self.user, organization=self.organization, is_active=True
            ).exists()
        )
        self.assertEqual(self.tenant_get().status_code, 302)

    @override_settings(
        DEBUG=False, TESTING=False, TOSEND_WEBHOOK_SECRET=""
    )  # nosec B106
    def test_api_sessions_are_guarded_but_anonymous_api_and_webhook_reach_views(self):
        api_path = "/api/v1/kiosk/999999/ping/"
        self.assertNotEqual(self.tenant_get(api_path).status_code, 403)
        self.client.force_login(self.user)
        self.assertEqual(self.tenant_get(api_path).status_code, 403)
        for path in ("/api/webhooks/tosend/", "/programs/webhooks/tosend/"):
            with self.subTest(path=path):
                response = self.client.post(
                    path,
                    data="{}",
                    content_type="application/json",
                    HTTP_HOST=self.host,
                )
                self.assertEqual(response.status_code, 401)

    def test_root_and_unknown_hosts_keep_existing_routing(self):
        self.client.force_login(self.user)
        self.assertEqual(
            self.client.get("/", HTTP_HOST="momentumteam.app").status_code, 200
        )
        self.assertEqual(
            self.client.get("/", HTTP_HOST="unknown.momentumteam.app").status_code,
            404,
        )

    def test_middleware_order(self):
        login = settings.MIDDLEWARE.index(
            "GoSAdminPortal.middleware.LoginRequiredMiddleware"
        )
        self.assertEqual(
            settings.MIDDLEWARE[login + 1 : login + 3],
            [
                "GoSAdminPortal.middleware.TenantMembershipGuardMiddleware",
                "GoSAdminPortal.middleware.MentorAgreementMiddleware",
            ],
        )

    def test_lightweight_paths_do_not_resolve_user_or_query_membership(self):
        from GoSAdminPortal.middleware import TenantMembershipGuardMiddleware

        def unexpected_user_access():
            raise AssertionError("lightweight request resolved the session user")

        middleware = TenantMembershipGuardMiddleware(lambda request: HttpResponse("ok"))
        for path in (
            settings.STATIC_URL + "asset.css",
            settings.MEDIA_URL + "file",
            "/health",
            "/health/",
        ):
            with self.subTest(path=path):
                request = RequestFactory().get(path)
                request.organization = self.organization
                request.user = SimpleLazyObject(unexpected_user_access)
                with self.assertNumQueries(0):
                    self.assertEqual(middleware(request).status_code, 200)
