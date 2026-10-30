"""Phase 4 tests: Extracting hardcoded GoS constants to OrganizationSettings.

Verifies:
1. Default constants (org name, Champion label, Lead Mentor label, clearance types,
   sliding scale config, email settings, feature flags) fall back properly.
2. Custom settings can be stored via key-value or typed properties on OrganizationSettings
   and Organization.settings.
3. request.organization.settings provides dictionary and dot-attribute access in Python
   and Django templates.
4. Templates and views read organization settings and name dynamically.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.template import Context, Template
from django.test import RequestFactory, TestCase

from organizations.models import (
    DEFAULT_ORGANIZATION_SETTINGS,
    Organization,
    OrganizationSettings,
    SettingsDict,
)
from organizations.utils import (
    get_organization_email_settings,
    get_organization_setting,
    get_organization_settings,
)


class OrganizationSettingsModelTests(TestCase):
    def setUp(self):
        self.gos = Organization.objects.get(slug="gos")
        self.rival = Organization.objects.create(
            name="Rival Robotics",
            slug="rival",
            settings={
                "champion_label": "Lead Student",
                "lead_mentor_label": "Head Coach",
            },
        )
        self.rival_settings, _ = OrganizationSettings.objects.get_or_create(
            organization=self.rival
        )

    def test_default_constants_fallback(self):
        """Unset settings fall back to DEFAULT_ORGANIZATION_SETTINGS."""
        settings = self.gos.settings
        self.assertEqual(settings.get("champion_label"), "Champion")
        self.assertEqual(settings.get("lead_mentor_label"), "Lead Mentor")
        self.assertEqual(settings.get("helper_label"), "Helper")
        self.assertEqual(settings.get("mentor_label"), "Mentor")
        self.assertEqual(settings.get("parent_label"), "Parent")
        self.assertEqual(settings.get("student_label"), "Student")
        self.assertIn("outreach", settings.get("feature_flags", {}))
        self.assertTrue(settings.get("feature_flags")["outreach"])
        self.assertEqual(len(settings.get("clearance_types", [])), 3)

    def test_custom_settings_override(self):
        """Custom settings override defaults."""
        settings = self.rival.settings
        self.assertEqual(settings.get("champion_label"), "Lead Student")
        self.assertEqual(settings.get("lead_mentor_label"), "Head Coach")
        # Unset keys still fall back
        self.assertEqual(settings.get("helper_label"), "Helper")

    def test_dot_attribute_access_on_settings_dict(self):
        """SettingsDict supports dot-attribute access for templates and Python."""
        self.assertEqual(self.gos.settings.champion_label, "Champion")
        self.assertEqual(self.gos.settings.lead_mentor_label, "Lead Mentor")
        self.assertEqual(self.rival.settings.champion_label, "Lead Student")
        self.assertEqual(self.rival.settings.lead_mentor_label, "Head Coach")

    def test_typed_properties_on_organization_settings_model(self):
        """OrganizationSettings model provides typed properties and methods."""
        org_settings = OrganizationSettings.objects.get(organization=self.gos)
        self.assertEqual(org_settings.champion_label, "Champion")
        self.assertEqual(org_settings.lead_mentor_label, "Lead Mentor")
        self.assertEqual(org_settings.helper_label, "Helper")
        self.assertEqual(org_settings.mentor_label, "Mentor")
        self.assertEqual(org_settings.parent_label, "Parent")
        self.assertEqual(org_settings.student_label, "Student")

        # Sliding scale config
        ss_config = org_settings.sliding_scale_config
        self.assertEqual(ss_config["base_amount"], "10150.00")
        self.assertEqual(org_settings.sliding_scale_base_amount, Decimal("10150.00"))
        self.assertEqual(
            org_settings.sliding_scale_additional_member_amount, Decimal("5500.00")
        )
        self.assertEqual(org_settings.sliding_scale_low_multiplier, Decimal("1.50"))
        self.assertEqual(org_settings.sliding_scale_high_multiplier, Decimal("4.00"))

        # Feature flags and clearance types
        self.assertIn("travel", org_settings.feature_flags)
        self.assertEqual(len(org_settings.clearance_types), 3)

    def test_set_setting_method(self):
        """set_setting updates and saves setting values."""
        self.gos.set_setting("champion_label", "Shift Captain")
        self.assertEqual(self.gos.get_setting("champion_label"), "Shift Captain")

        org_settings = OrganizationSettings.objects.get(organization=self.gos)
        org_settings.set_setting("lead_mentor_label", "Team Director")
        self.assertEqual(org_settings.lead_mentor_label, "Team Director")

    def test_utils_get_organization_setting(self):
        """get_organization_setting utility resolves settings with fallback."""
        self.assertEqual(
            get_organization_setting(self.gos, "champion_label"), "Champion"
        )
        self.assertEqual(
            get_organization_setting(self.rival, "champion_label"), "Lead Student"
        )
        self.assertEqual(get_organization_setting(None, "champion_label"), "Champion")
        self.assertEqual(
            get_organization_setting(None, "nonexistent", "DefaultVal"), "DefaultVal"
        )


class TemplateSettingsAccessTests(TestCase):
    def setUp(self):
        self.gos = Organization.objects.get(slug="gos")
        self.rival = Organization.objects.create(
            name="Rival Robotics",
            slug="rival",
            settings={
                "champion_label": "Lead Student",
                "lead_mentor_label": "Head Coach",
            },
        )
        self.rf = RequestFactory()

    def test_template_rendering_with_organization_settings(self):
        """Templates can access request.organization.settings.<key> and request.organization.name."""
        template = Template(
            "Portal: {{ request.organization.name }} | "
            "Champion: {{ request.organization.settings.champion_label }} | "
            "Lead: {{ request.organization.settings.lead_mentor_label }}"
        )

        req_gos = self.rf.get("/")
        req_gos.organization = self.gos
        rendered_gos = template.render(Context({"request": req_gos}))
        self.assertIn("Portal: Girls of Steel", rendered_gos)
        self.assertIn("Champion: Champion", rendered_gos)
        self.assertIn("Lead: Lead Mentor", rendered_gos)

        req_rival = self.rf.get("/")
        req_rival.organization = self.rival
        rendered_rival = template.render(Context({"request": req_rival}))
        self.assertIn("Portal: Rival Robotics", rendered_rival)
        self.assertIn("Champion: Lead Student", rendered_rival)
        self.assertIn("Lead: Head Coach", rendered_rival)
