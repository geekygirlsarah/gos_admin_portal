"""Tests for the Momentum root marketing home page and tenant routing."""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from organizations.models import Organization
from organizations.utils import get_organization_url


@override_settings(
    MOMENTUM_ROOT_DOMAIN="momentumteam.app",
    ALLOWED_HOSTS=[
        "localhost",
        ".localhost",
        "127.0.0.1",
        "testserver",
        "momentumteam.app",
        ".momentumteam.app",
        "portal.girlsofsteelrobotics.org",
        "gos.example.org",
        "robotigers.example.org",
        "unknown.example.org",
        "robotigers.attacker.example",
    ],
)
class MarketingHomePageTests(TestCase):
    def setUp(self):
        self.gos = Organization.objects.get(slug="gos")
        self.other_org = Organization.objects.create(
            name="RoboTigers",
            slug="robotigers",
            subdomain="robotigers",
            is_active=True,
        )
        User = get_user_model()
        self.user = User.objects.create_user(
            username="student_user",
            email="student@example.com",
            password="password123",  # nosec B106
        )

    @override_settings(MOMENTUM_ROOT_DOMAIN="momentumteam.app")
    def test_get_organization_url_uses_configured_domains_and_routable_fallbacks(self):
        self.other_org.settings = {
            "custom_domain": {
                "enabled": True,
                "domain": "robotigers.example.org",
            }
        }
        self.assertEqual(
            get_organization_url(self.other_org), "https://robotigers.example.org"
        )

        self.other_org.settings = {"custom_domain": "https://team.example.org"}
        self.assertEqual(
            get_organization_url(self.other_org), "https://team.example.org"
        )

        self.other_org.settings = {}
        self.other_org.subdomain = "robotigers-team"
        self.assertEqual(
            get_organization_url(self.other_org),
            "https://robotigers-team.momentumteam.app",
        )

        self.other_org.subdomain = ""
        self.assertEqual(
            get_organization_url(self.other_org),
            "https://robotigers.momentumteam.app",
        )

    def test_marketing_team_links_use_active_organization_urls(self):
        self.gos.settings = {"custom_domain": "gos.example.org"}
        self.gos.save(update_fields=["settings"])
        self.other_org.subdomain = "robotigers-team"
        self.other_org.settings = {
            "custom_domain": {
                "enabled": True,
                "domain": "robotigers.example.org",
            }
        }
        self.other_org.save(update_fields=["subdomain", "settings"])
        Organization.objects.create(
            name="Inactive Team",
            slug="inactive-team",
            subdomain="inactive-team",
            is_active=False,
        )

        for path in ("/", reverse("landing"), reverse("about")):
            with self.subTest(path=path):
                response = self.client.get(path, HTTP_HOST="momentumteam.app")
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'href="https://gos.example.org"')
                self.assertContains(response, 'href="https://robotigers.example.org"')
                self.assertContains(response, "gos.example.org")
                self.assertContains(response, "robotigers.example.org")
                self.assertContains(
                    response, 'data-team-domain="robotigers.example.org"'
                )
                self.assertNotContains(response, "Inactive Team")

    def test_custom_domain_host_resolves_to_its_organization(self):
        self.other_org.settings = {
            "custom_domain": {
                "enabled": True,
                "domain": "robotigers.example.org",
            }
        }
        self.other_org.save(update_fields=["settings"])

        response = self.client.get("/", HTTP_HOST="robotigers.example.org")

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith("/accounts/login/"))
        self.assertEqual(response.wsgi_request.organization, self.other_org)

    def test_organization_header_cannot_override_the_request_host(self):
        response = self.client.get(
            "/accounts/login/",
            HTTP_HOST="gos.momentumteam.app",
            HTTP_X_ORG_SLUG="robotigers",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.wsgi_request.organization, self.gos)

    def test_unrelated_host_cannot_resolve_by_its_first_label(self):
        response = self.client.get(
            "/accounts/login/", HTTP_HOST="robotigers.attacker.example"
        )

        self.assertEqual(response.status_code, 404)
        self.assertIsNone(response.wsgi_request.organization)

    def test_reserved_subdomain_label_cannot_resolve_to_an_organization(self):
        Organization.objects.create(name="Admin Team", slug="admin", subdomain="admin")

        response = self.client.get(
            "/accounts/login/", HTTP_HOST="admin.momentumteam.app"
        )

        self.assertEqual(response.status_code, 404)
        self.assertIsNone(response.wsgi_request.organization)

    def test_unknown_and_inactive_tenant_hosts_do_not_use_session_or_default_org(self):
        session = self.client.session
        session["active_organization_id"] = self.other_org.pk
        session.save()
        Organization.objects.create(
            name="Inactive Team",
            slug="inactive-team",
            subdomain="inactive-team",
            is_active=False,
        )

        for host in (
            "unknown.momentumteam.app",
            "unknown.example.org",
            "inactive-team.momentumteam.app",
        ):
            with self.subTest(host=host):
                cache.delete(f"org_resolve:host:{host}")
                response = self.client.get("/accounts/login/", HTTP_HOST=host)
                self.assertEqual(response.status_code, 404)
                self.assertIsNone(response.wsgi_request.organization)

    def test_cached_unknown_host_does_not_use_session_or_default_org(self):
        session = self.client.session
        session["active_organization_id"] = self.other_org.pk
        session.save()
        cache.set("org_resolve:host:unknown.example.org", False, timeout=300)

        response = self.client.get("/accounts/login/", HTTP_HOST="unknown.example.org")

        self.assertEqual(response.status_code, 404)
        self.assertIsNone(response.wsgi_request.organization)

    def test_cached_host_mapping_is_revalidated_against_organization_settings(self):
        self.other_org.settings = {
            "custom_domain": {
                "enabled": True,
                "domain": "robotigers.example.org",
            }
        }
        self.other_org.save(update_fields=["settings"])
        cache.set(
            "org_resolve:host:robotigers.example.org", self.other_org.pk, timeout=300
        )
        Organization.objects.filter(pk=self.other_org.pk).update(settings={})

        response = self.client.get(
            "/accounts/login/", HTTP_HOST="robotigers.example.org"
        )

        self.assertEqual(response.status_code, 404)
        self.assertIsNone(response.wsgi_request.organization)

    @override_settings(ALLOWED_HOSTS=["momentumteam.app"])
    def test_django_rejects_host_outside_allowed_hosts(self):
        response = self.client.get("/", HTTP_HOST="untrusted.example.net")

        self.assertEqual(response.status_code, 400)

    def test_root_domain_anonymous_gets_marketing_page(self):
        """Anonymous request to root domain momentumteam.app renders marketing landing page."""
        response = self.client.get("/", HTTP_HOST="momentumteam.app")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "marketing/landing.html")
        self.assertContains(response, "Empower Your Robotics Team with")
        self.assertContains(response, "Momentum")
        self.assertContains(response, "Girls of Steel")
        self.assertContains(response, "RoboTigers")
        self.assertContains(response, "robotigers.momentumteam.app")
        # Coming soon and pilot preview messaging
        self.assertContains(response, "Coming Soon")
        self.assertContains(response, "Pilot Preview")
        self.assertContains(response, "Momentum is Currently in Pilot Access")
        # High contrast Explore Features button
        self.assertContains(response, "btn btn-light btn-lg px-4 fw-bold text-dark")
        # Scalable team search input and scrollable container
        self.assertContains(response, 'id="teamSearchInput"')
        self.assertContains(response, 'id="teamListContainer"')
        self.assertContains(response, "team-list-scroll")
        # Taglines and marketing copy from marketing.md
        self.assertContains(response, "From first application to final robot run")
        self.assertContains(
            response, "From Spreadsheets to a Real Team Operating System"
        )
        self.assertContains(response, "Build Season is Chaotic Enough")
        self.assertContains(response, "Attendance Hours are Real Data, Not Vibes")
        self.assertContains(response, "Orders & Procurement Deserve a System")
        self.assertContains(response, "Public Application Wizard")
        self.assertContains(response, "Kiosk Attendance & Hours Dashboard")
        self.assertContains(response, "Frequently Asked Questions")
        # Ensure pricing and tiers are not included
        self.assertNotContains(response, "$25")
        self.assertNotContains(response, "$45")
        self.assertNotContains(response, "Founding Tier")
        self.assertNotContains(response, "/month")

    def test_www_root_domain_gets_marketing_page(self):
        """Request to www.momentumteam.app renders marketing landing page."""
        response = self.client.get("/", HTTP_HOST="www.momentumteam.app")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "marketing/landing.html")

    def test_localhost_root_domain_gets_marketing_page(self):
        """Request to root dev host localhost:8000 renders marketing landing page."""
        response = self.client.get("/", HTTP_HOST="localhost:8000")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "marketing/landing.html")

    def test_localhost_subdomain_anonymous_redirects_to_login(self):
        """Anonymous request to gos.localhost:8000 redirects to login."""
        response = self.client.get("/", HTTP_HOST="gos.localhost:8000")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith("/accounts/login/"))

    def test_explicit_landing_and_about_urls(self):
        """Explicit /landing/ and /about/ routes render the marketing landing page."""
        for url_name in ("landing", "about"):
            response = self.client.get(reverse(url_name), HTTP_HOST="momentumteam.app")
            self.assertEqual(response.status_code, 200)
            self.assertTemplateUsed(response, "marketing/landing.html")

    def test_gos_subdomain_anonymous_redirects_to_login(self):
        """Anonymous request to gos.momentumteam.app redirects to login."""
        response = self.client.get("/", HTTP_HOST="gos.momentumteam.app")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith("/accounts/login/"))

    def test_gos_subdomain_authenticated_redirects_to_dashboard(self):
        """Authenticated request to gos.momentumteam.app root redirects to profile dashboard."""
        self.client.force_login(self.user)
        response = self.client.get("/", HTTP_HOST="gos.momentumteam.app")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("profile_dashboard"))

    def test_gos_legacy_cname_redirects_to_login_for_anonymous(self):
        """Anonymous request to portal.girlsofsteelrobotics.org acts as GoS tenant site."""
        response = self.client.get("/", HTTP_HOST="portal.girlsofsteelrobotics.org")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith("/accounts/login/"))

    def test_navbar_and_footer_branding_on_root_platform(self):
        """On root platform without organization, branding uses Momentum titles."""
        response = self.client.get("/", HTTP_HOST="momentumteam.app")
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, "Momentum | Operations Platform for FIRST® Robotics Teams"
        )
        self.assertContains(
            response, "The operations platform for FIRST&reg; robotics teams."
        )

    def test_navbar_and_footer_branding_on_tenant_subdomain(self):
        """On tenant subdomain, branding uses organization name."""
        self.client.force_login(self.user)
        response = self.client.get(
            reverse("profile_dashboard"), HTTP_HOST="gos.momentumteam.app"
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Girls of Steel Portal")
