"""Phase 1 multi-tenant groundwork tests.

Verifies the Organization model + seeded GoS row, the OrganizationMiddleware
that hardcodes ``request.organization``, and the ``ProgramQuerySet``
``for_organization()`` helper (including "zero behavior change": unscoped rows
stay visible while scoping is not yet enforced).
"""

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.urls import reverse

from organizations.models import Organization
from programs.models import Program
from programs.views.programs import ProgramListView


class OrganizationModelTests(TestCase):
    def test_defaults(self):
        org = Organization.objects.create(name="Test Org", slug="test-org")
        self.assertTrue(org.is_active)
        self.assertIsNone(org.subdomain)
        self.assertEqual(org.settings, {})
        self.assertEqual(str(org), "Test Org")

    def test_slug_unique(self):
        Organization.objects.create(name="First", slug="dup")
        with self.assertRaises(Exception):
            Organization.objects.create(name="Second", slug="dup")


class GosOrganizationSeededByMigrationTests(TestCase):
    def test_gos_organization_exists(self):
        org = Organization.objects.filter(slug="gos").first()
        self.assertIsNotNone(org)
        self.assertEqual(org.name, "Girls of Steel")
        self.assertTrue(org.is_active)


class OrganizationMiddlewareTests(TestCase):
    def test_request_organization_is_gos(self):
        from GoSAdminPortal.middleware import OrganizationMiddleware

        request = RequestFactory().get("/health/")
        middleware = OrganizationMiddleware(lambda req: None)
        middleware(request)

        self.assertIsNotNone(request.organization)
        self.assertEqual(request.organization.slug, "gos")

    def test_dashboard_request_gets_organization(self):
        # Full-stack: the middleware runs on real requests too.
        user = get_user_model().objects.create_superuser(
            username="boss", email="boss@example.com", password="pw"  # nosec B106
        )
        self.client.force_login(user)
        response = self.client.get(reverse("profile_dashboard"))
        self.assertEqual(response.status_code, 200)


class ProgramForOrganizationTests(TestCase):
    def setUp(self):
        self.gos = Organization.objects.get(slug="gos")
        self.other = Organization.objects.create(name="Other Org", slug="other")

    def test_for_organization_none_returns_all(self):
        Program.objects.create(name="P1", organization=self.gos)
        Program.objects.create(name="P2")
        self.assertEqual(Program.objects.for_organization(None).count(), 2)

    def test_for_organization_keeps_matching_and_unscoped(self):
        Program.objects.create(name="Mine", organization=self.gos)
        Program.objects.create(name="Unscoped")
        Program.objects.create(name="Rival", organization=self.other)

        names = set(
            Program.objects.for_organization(self.gos).values_list("name", flat=True)
        )
        self.assertEqual(names, {"Mine", "Unscoped"})

    def test_program_list_view_scopes_to_organization(self):
        Program.objects.create(name="GoS Program", organization=self.gos, active=True)
        Program.objects.create(name="Unscoped Program", active=True)
        Program.objects.create(
            name="Rival Program", organization=self.other, active=True
        )

        user = get_user_model().objects.create_superuser(
            username="lead", email="lead@example.com", password="pw"  # nosec B106
        )
        request = RequestFactory().get("/profile/")
        request.user = user
        request.organization = self.gos

        view = ProgramListView()
        view.setup(request)
        self.assertEqual(view.get_queryset().count(), 2)

    def test_home_page_hides_other_org_programs(self):
        Program.objects.create(name="GoS Program", organization=self.gos, active=True)
        Program.objects.create(
            name="Rival Program", organization=self.other, active=True
        )

        user = get_user_model().objects.create_superuser(
            username="lead2", email="lead2@example.com", password="pw"  # nosec B106
        )
        self.client.force_login(user)
        response = self.client.get(reverse("profile_dashboard"))
        self.assertContains(response, "GoS Program")
        self.assertNotContains(response, "Rival Program")
