from django.test import TestCase, override_settings

from organizations.models import Organization

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
