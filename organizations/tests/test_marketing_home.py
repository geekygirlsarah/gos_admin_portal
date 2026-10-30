"""Tests for the Momentum root marketing home page and tenant routing."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from organizations.models import Organization


@override_settings(
    ALLOWED_HOSTS=[
        "localhost",
        ".localhost",
        "127.0.0.1",
        "testserver",
        "momentumteam.app",
        ".momentumteam.app",
        "portal.girlsofsteelrobotics.org",
    ]
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
