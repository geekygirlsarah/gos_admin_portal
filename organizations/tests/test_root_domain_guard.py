from django.contrib.auth.models import Group, User
from django.test import TestCase, override_settings

from organizations.models import Organization, UserOrganizationMembership
from programs.models import Program

ROOT = "momentumteam.app"


@override_settings(MOMENTUM_ROOT_DOMAIN=ROOT, ALLOWED_HOSTS=["*"])
class RootDomainGuardTests(TestCase):
    def test_tenant_paths_blocked_on_root_domain(self):
        for path in (
            "/programs/",
            "/programs/1/orders/",
            "/programs/1/travel/",
            "/badges/",
            "/attendance/",
            "/calendar/",
            "/apply/",
            "/profile/",
        ):
            with self.subTest(path=path):
                response = self.client.get(path, HTTP_HOST=ROOT)
                self.assertRedirects(response, "/", fetch_redirect_response=False)

    def test_marketing_and_platform_paths_reachable_on_root_domain(self):
        for path in ("/", "/landing/", "/about/", "/health/"):
            with self.subTest(path=path):
                response = self.client.get(path, HTTP_HOST=ROOT)
                self.assertEqual(response.status_code, 200)

    def test_platform_admin_not_blocked_on_root_domain(self):
        response = self.client.get("/platform-admin/", HTTP_HOST=ROOT)
        self.assertNotEqual(response.status_code, 404)

    def test_tenant_paths_not_blocked_on_org_domain(self):
        Organization.objects.get_or_create(
            slug="guard-org", defaults={"name": "Guard Org", "subdomain": "guard-org"}
        )
        response = self.client.get("/programs/", HTTP_HOST=f"guard-org.{ROOT}")
        self.assertNotEqual(response.status_code, 404)

    def test_unknown_host_is_rejected_instead_of_redirected(self):
        response = self.client.get(
            "/accounts/login/", HTTP_HOST="unknown.momentumteam.app"
        )

        self.assertEqual(response.status_code, 404)

    def test_root_and_tenant_subdomains_stay_isolated_across_requests(self):
        alpha = Organization.objects.create(
            name="Alpha Host", slug="alpha-host", subdomain="alpha-host"
        )
        beta = Organization.objects.create(
            name="Beta Host", slug="beta-host", subdomain="beta-host"
        )
        Program.objects.create(
            organization=alpha, name="Alpha Host Program", active=True
        )
        Program.objects.create(organization=beta, name="Beta Host Program", active=True)
        user = User.objects.create_user(
            username="alpha-host-lead", password="password123"
        )  # nosec B106
        lead_group, _ = Group.objects.get_or_create(name="LeadMentor")
        user.groups.add(lead_group)
        UserOrganizationMembership.objects.create(
            user=user, organization=alpha, role="LeadMentor", is_active=True
        )
        self.client.force_login(user)

        root_response = self.client.get("/programs/", HTTP_HOST=ROOT)
        self.assertRedirects(root_response, "/", fetch_redirect_response=False)
        self.assertIsNone(root_response.wsgi_request.organization)

        alpha_response = self.client.get("/programs/", HTTP_HOST=f"alpha-host.{ROOT}")
        self.assertEqual(alpha_response.status_code, 200)
        self.assertEqual(alpha_response.wsgi_request.organization, alpha)
        self.assertContains(alpha_response, "Alpha Host Program")
        self.assertNotContains(alpha_response, "Beta Host Program")

        beta_response = self.client.get("/programs/", HTTP_HOST=f"beta-host.{ROOT}")
        self.assertEqual(beta_response.status_code, 403)
        self.assertEqual(beta_response.wsgi_request.organization, beta)
        self.assertNotIn(b"Beta Host Program", beta_response.content)
