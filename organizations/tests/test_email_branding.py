from io import BytesIO

from django.contrib.auth.models import Group, User
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.template.loader import render_to_string
from django.test import TestCase
from django.urls import reverse
from PIL import Image

from organizations.models import Organization, OrganizationSettings
from organizations.utils import (
    get_organization_email_settings,
    update_organization_email_settings,
)
from programs.models import Adult
from programs.utils.notifications import send_templated_notification


class OrganizationEmailBrandingTests(TestCase):
    def setUp(self):
        self.org, _ = Organization.objects.get_or_create(
            slug="gos", defaults={"name": "Girls of Steel Robotics"}
        )
        self.org_settings, _ = OrganizationSettings.objects.get_or_create(
            organization=self.org
        )

        # Setup lead mentor user
        self.lead_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.user = User.objects.create_user(
            username="lead_mentor",
            email="lead@girlsofsteelrobotics.org",
            password="testpassword123",  # nosec B106
        )
        self.user.groups.add(self.lead_group)
        self.adult = Adult.objects.create(
            user=self.user,
            legal_first_name="Lead",
            last_name="Mentor",
            personal_email="lead@girlsofsteelrobotics.org",
        )
        self.url = reverse("portal_email_settings")

    def test_default_branding_settings(self):
        cfg = get_organization_email_settings(self.org)
        self.assertEqual(cfg["primary_color"], "#4a154b")
        self.assertEqual(cfg["header_text_color"], "#ffffff")
        self.assertEqual(cfg["organization_name"], self.org.name)
        self.assertEqual(cfg["address_line1"], "Carnegie Mellon University")
        self.assertEqual(
            cfg["address_line2"], "5000 Forbes Ave, Pittsburgh, PA 15213-3890"
        )
        self.assertEqual(
            cfg["footer_text"],
            "Thank you for being part of Girls of Steel Robotics!",
        )
        self.assertEqual(cfg["contact_email"], "leads@girlsofsteelrobotics.org")

    def test_custom_branding_settings(self):
        custom_cfg = {
            "primary_color": "#123456",
            "secondary_color": "#abcdef",
            "header_text_color": "#fefefe",
            "logo_url": "https://example.com/logo.png",
            "organization_name": "Team Hyperion",
            "address_line1": "123 Tech Way",
            "address_line2": "Suite 400",
            "footer_text": "Inspiring tomorrow's engineers.",
            "contact_name": "Jane Smith",
            "contact_email": "contact@teamhyperion.org",
        }
        update_organization_email_settings(self.org, custom_cfg)
        cfg = get_organization_email_settings(self.org)

        self.assertEqual(cfg["primary_color"], "#123456")
        self.assertEqual(cfg["secondary_color"], "#abcdef")
        self.assertEqual(cfg["header_text_color"], "#fefefe")
        self.assertEqual(cfg["logo_url"], "https://example.com/logo.png")
        self.assertEqual(cfg["organization_name"], "Team Hyperion")
        self.assertEqual(cfg["address_line1"], "123 Tech Way")
        self.assertEqual(cfg["address_line2"], "Suite 400")
        self.assertEqual(cfg["footer_text"], "Inspiring tomorrow's engineers.")
        self.assertEqual(cfg["contact_name"], "Jane Smith")
        self.assertEqual(cfg["contact_email"], "contact@teamhyperion.org")

    def test_portal_settings_update_branding_post(self):
        self.client.login(
            username="lead_mentor", password="testpassword123"
        )  # nosec B106
        post_data = {
            "action": "update_branding",
            "primary_color": "#0055aa",
            "secondary_color": "#f0f0f0",
            "header_text_color": "#ffffff",
            "organization_name": "RoboTitans",
            "address_line1": "456 Innovation Blvd",
            "address_line2": "Building B",
            "footer_text": "Building the future.",
            "contact_name": "Alex Taylor",
            "contact_email": "leads@robotitans.org",
            "logo_url": "https://example.org/static/logo.png",
        }
        resp = self.client.post(self.url, post_data)
        self.assertEqual(resp.status_code, 302)

        self.org_settings.refresh_from_db()
        cfg = self.org_settings.data.get("email_settings", {})
        self.assertEqual(cfg.get("primary_color"), "#0055aa")
        self.assertEqual(cfg.get("organization_name"), "RoboTitans")
        self.assertEqual(cfg.get("address_line1"), "456 Innovation Blvd")
        self.assertEqual(cfg.get("logo_url"), "https://example.org/static/logo.png")

    def test_portal_settings_upload_logo_file(self):
        self.client.login(
            username="lead_mentor", password="testpassword123"
        )  # nosec B106

        # Create a small valid test PNG image
        image_io = BytesIO()
        image = Image.new("RGBA", (100, 100), color=(255, 0, 0, 255))
        image.save(image_io, format="PNG")
        image_io.seek(0)

        uploaded_logo = SimpleUploadedFile(
            "team_logo.png",
            image_io.getvalue(),
            content_type="image/png",
        )

        post_data = {
            "action": "update_branding",
            "primary_color": "#4a154b",
            "organization_name": "Girls of Steel Robotics",
            "logo_file": uploaded_logo,
        }
        resp = self.client.post(self.url, post_data)
        self.assertEqual(resp.status_code, 302)

        self.org_settings.refresh_from_db()
        cfg = self.org_settings.data.get("email_settings", {})
        self.assertTrue(cfg.get("logo_url", "").endswith(".png"))

    def test_portal_settings_remove_logo(self):
        self.org_settings.data = {
            "email_settings": {
                "logo_url": "https://example.com/old_logo.png",
            }
        }
        self.org_settings.save()

        self.client.login(
            username="lead_mentor", password="testpassword123"
        )  # nosec B106
        post_data = {
            "action": "update_branding",
            "primary_color": "#4a154b",
            "remove_logo": "1",
        }
        resp = self.client.post(self.url, post_data)
        self.assertEqual(resp.status_code, 302)

        self.org_settings.refresh_from_db()
        cfg = self.org_settings.data.get("email_settings", {})
        self.assertEqual(cfg.get("logo_url"), "")

    def test_base_notification_template_renders_branding(self):
        cfg = {
            "primary_color": "#7b1fa2",
            "header_text_color": "#ffffff",
            "logo_url": "https://example.org/test-logo.png",
            "organization_name": "Steel City Robotics",
            "address_line1": "100 Forbes Ave",
            "address_line2": "Pittsburgh, PA",
            "footer_text": "Custom Footer Note",
            "contact_name": "Coach Kelly",
            "contact_email": "coach@steelcity.org",
        }
        context = {
            "org_email_settings": cfg,
            "organization": self.org,
        }
        rendered = render_to_string("programs/emails/base_notification.html", context)

        self.assertIn("#7b1fa2", rendered)
        self.assertIn("https://example.org/test-logo.png", rendered)
        self.assertIn("Steel City Robotics", rendered)
        self.assertIn("100 Forbes Ave", rendered)
        self.assertIn("Custom Footer Note", rendered)
        self.assertIn("coach@steelcity.org", rendered)

    def test_send_templated_notification_uses_branding(self):
        self.org_settings.data = {
            "email_settings": {
                "primary_color": "#336699",
                "organization_name": "RoboPanthers",
                "footer_text": "Panther Pride",
            }
        }
        self.org_settings.save()

        send_templated_notification(
            subject="Welcome RoboPanthers!",
            template_name="programs/emails/base_notification.html",
            context={},
            recipient_list=["student@example.com"],
            organization=self.org,
        )

        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertEqual(email.subject, "Welcome RoboPanthers!")
        # HTML alternative contains custom branding
        html_content = email.alternatives[0][0]
        self.assertIn("#336699", html_content)
        self.assertIn("RoboPanthers", html_content)
        self.assertIn("Panther Pride", html_content)

    def test_portal_settings_page_renders_branding_controls(self):
        self.client.login(
            username="lead_mentor", password="testpassword123"
        )  # nosec B106
        resp = self.client.get(reverse("portal_settings") + "?tab=email")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Email Branding &amp; Template Customization")
        self.assertContains(resp, "Primary Brand Color")
        self.assertContains(resp, "Organization Logo")
        self.assertContains(resp, "Email Template Live Preview")
