from io import BytesIO

from django.conf import settings
from django.contrib.auth.models import Group, User
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from PIL import Image

from organizations.models import (
    Organization,
    OrganizationDomain,
    OrganizationSettings,
    UserOrganizationMembership,
)
from organizations.utils import (
    get_organization_email_settings,
    get_organization_url,
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
        UserOrganizationMembership.objects.get_or_create(
            user=self.user,
            organization=self.org,
            role="LeadMentor",
            defaults={"is_active": True},
        )
        self.url = reverse("portal_email_settings")

    def test_default_branding_settings(self):
        organization = Organization.objects.create(
            name="Nova Robotics", slug="nova-branding-defaults"
        )
        cfg = get_organization_email_settings(organization)
        self.assertEqual(cfg["primary_color"], "#334155")
        self.assertEqual(cfg["header_text_color"], "#ffffff")
        self.assertEqual(cfg["organization_name"], organization.name)
        self.assertEqual(cfg["address_line1"], "")
        self.assertEqual(cfg["address_line2"], "")
        self.assertEqual(cfg["footer_text"], "")
        self.assertEqual(cfg["contact_name"], "")
        self.assertEqual(cfg["contact_email"], settings.DEFAULT_REPLY_TO_EMAIL)
        self.assertEqual(cfg["reply_to_email"], settings.DEFAULT_REPLY_TO_EMAIL)
        self.assertIn("portal_url", cfg)

    def test_custom_branding_settings(self):
        self.org_settings.data = {
            "email_settings": {"portal_url": "https://portal.teamhyperion.org"}
        }
        self.org_settings.save()
        custom_cfg = {
            "primary_color": "#123456",
            "secondary_color": "#abcdef",
            "header_text_color": "#fefefe",
            "logo_url": "https://example.com/logo.png",
            "portal_url": "https://portal.teamhyperion.org",
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
        self.assertEqual(cfg["portal_url"], get_organization_url(self.org))
        self.org_settings.refresh_from_db()
        self.assertEqual(
            self.org_settings.data["email_settings"]["portal_url"],
            "https://portal.teamhyperion.org",
        )
        self.assertEqual(cfg["organization_name"], "Team Hyperion")
        self.assertEqual(cfg["address_line1"], "123 Tech Way")
        self.assertEqual(cfg["address_line2"], "Suite 400")
        self.assertEqual(cfg["footer_text"], "Inspiring tomorrow's engineers.")
        self.assertEqual(cfg["contact_name"], "Jane Smith")
        self.assertEqual(cfg["contact_email"], "contact@teamhyperion.org")

    def test_portal_settings_update_branding_post(self):
        self.org_settings.data = {
            "email_settings": {"portal_url": "https://old.example.com"}
        }
        self.org_settings.save()
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
            "portal_url": "https://portal.robotitans.org",
        }
        resp = self.client.post(self.url, post_data)
        self.assertEqual(resp.status_code, 302)

        self.org_settings.refresh_from_db()
        cfg = self.org_settings.data.get("email_settings", {})
        self.assertEqual(cfg.get("primary_color"), "#0055aa")
        self.assertEqual(cfg.get("organization_name"), "RoboTitans")
        self.assertEqual(cfg.get("address_line1"), "456 Innovation Blvd")
        self.assertEqual(cfg.get("logo_url"), "https://example.org/static/logo.png")
        self.assertEqual(cfg.get("portal_url"), "https://old.example.com")

    @override_settings(
        STORAGES={
            "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
            "staticfiles": {
                "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
            },
        }
    )
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

    def test_optional_logos_are_saved_preserved_and_removed_independently(self):
        self.client.force_login(self.user)
        self.client.post(
            self.url,
            {
                "action": "update_branding",
                "logo_url": "https://example.com/icon.png",
                "wordmark_url": "https://example.com/wordmark.png",
                "host_logo_url": "https://example.com/host.png",
            },
        )
        cfg = get_organization_email_settings(self.org)
        self.assertEqual(cfg["wordmark_url"], "https://example.com/wordmark.png")
        self.assertEqual(cfg["host_logo_url"], "https://example.com/host.png")

        self.client.post(
            self.url, {"action": "update_branding", "remove_wordmark": "1"}
        )
        cfg = get_organization_email_settings(self.org)
        self.assertEqual(cfg["wordmark_url"], "")
        self.assertEqual(cfg["host_logo_url"], "https://example.com/host.png")
        self.assertEqual(cfg["logo_url"], "https://example.com/icon.png")

    def test_logo_context_is_organization_scoped_with_icon_fallbacks(self):
        from GoSAdminPortal.context_processors import organization_context

        update_organization_email_settings(
            self.org,
            {
                "logo_url": "https://example.com/icon.png",
                "wordmark_url": "https://example.com/wordmark.png",
                "host_logo_url": "https://example.com/host.png",
            },
        )
        request = RequestFactory().get("/")
        request.organization = self.org
        context = organization_context(request)
        self.assertEqual(
            context["organization_wordmark_url"], "https://example.com/wordmark.png"
        )
        self.assertEqual(
            context["organization_host_logo_url"], "https://example.com/host.png"
        )
        other = Organization.objects.create(name="Other", slug="other-branding")
        update_organization_email_settings(
            other, {"logo_url": "https://example.com/other.png"}
        )
        request.organization = other
        context = organization_context(request)
        self.assertEqual(
            context["organization_wordmark_url"], "https://example.com/other.png"
        )
        self.assertEqual(
            context["organization_host_logo_url"], "https://example.com/other.png"
        )

    def test_account_and_receipt_render_their_respective_logos(self):
        from GoSAdminPortal.context_processors import organization_context

        update_organization_email_settings(
            self.org,
            {
                "logo_url": "https://example.com/icon.png",
                "wordmark_url": "https://example.com/wordmark.png",
                "host_logo_url": "https://example.com/host.png",
            },
        )
        request = RequestFactory().get("/")
        request.organization = self.org
        context = organization_context(request)
        for template in (
            "account/login.html",
            "account/confirm_login_code.html",
            "account/logout.html",
        ):
            html = render_to_string(template, context)
            self.assertIn('src="https://example.com/wordmark.png"', html)
            self.assertIn('rel="icon" href="https://example.com/icon.png"', html)
        context.update(
            {"student": {"preferred_first_name": "Test"}, "program": {}, "payment": {}}
        )
        html = render_to_string("programs/payment_print.html", context)
        self.assertIn('src="https://example.com/host.png"', html)

    def test_printable_balance_sheet_uses_host_logo_with_icon_fallback(self):
        from GoSAdminPortal.context_processors import organization_context

        request = RequestFactory().get("/")
        request.organization = self.org
        for host, icon, expected in (
            (
                "https://example.com/host.png",
                "https://example.com/icon.png",
                "https://example.com/host.png",
            ),
            ("", "https://example.com/icon.png", "https://example.com/icon.png"),
            ("", "", ""),
        ):
            with self.subTest(host=host, icon=icon):
                update_organization_email_settings(
                    self.org, {"host_logo_url": host, "logo_url": icon}
                )
                context = organization_context(request)
                context.update(
                    {"student": {"preferred_first_name": "Test"}, "program": {}}
                )
                html = render_to_string("programs/balance_sheet_print.html", context)
                header = html.split('<div class="org-header text-end">', 1)[1].split(
                    "</div>", 1
                )[0]
                if expected:
                    self.assertIn(f'src="{expected}"', header)
                else:
                    self.assertNotIn("<img", header)

    @override_settings(
        STORAGES={
            "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
            "staticfiles": {
                "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
            },
        }
    )
    def test_optional_logo_uploads_reject_invalid_images(self):
        self.client.force_login(self.user)
        for role in ("wordmark", "host_logo"):
            with self.subTest(role=role):
                image_io = BytesIO()
                Image.new("RGB", (40, 20)).save(image_io, format="PNG")
                self.client.post(
                    self.url,
                    {
                        "action": "update_branding",
                        f"{role}_file": SimpleUploadedFile(
                            "logo.png", image_io.getvalue()
                        ),
                    },
                )
                saved = get_organization_email_settings(self.org)[f"{role}_url"]
                self.assertTrue(saved.endswith(".png"))
                self.client.post(
                    self.url,
                    {
                        "action": "update_branding",
                        f"{role}_file": SimpleUploadedFile("logo.png", b"not an image"),
                    },
                )
                self.assertEqual(
                    get_organization_email_settings(self.org)[f"{role}_url"], saved
                )

    def test_base_notification_template_renders_branding(self):
        cfg = {
            "primary_color": "#7b1fa2",
            "header_text_color": "#ffffff",
            "logo_url": "https://example.org/test-logo.png",
            "organization_name": "Steel City Robotics",
            "address_line1": "100 Forbes Ave",
            "address_line2": "Pittsburgh, PA",
            "footer_text": "Custom Footer Note",
            "portal_url": "https://portal.steelcity.org",
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
        self.assertIn("Pittsburgh, PA", rendered)
        self.assertIn("Custom Footer Note", rendered)
        self.assertIn("coach@steelcity.org", rendered)
        # Address and automated notice should be rendered inside the footer
        self.assertIn("email-footer", rendered)
        footer_portion = rendered.split('class="email-footer"')[1]
        self.assertIn("100 Forbes Ave", footer_portion)
        self.assertIn(
            '<a href="https://portal.steelcity.org" '
            'style="color: #7b1fa2; text-decoration: underline;">'
            "Steel City Robotics Portal</a>",
            footer_portion,
        )
        self.assertIn(
            'This is an automated notification from the <a href="https://portal.steelcity.org"',
            footer_portion,
        )

    @override_settings(MOMENTUM_ROOT_DOMAIN="momentumteam.app")
    def test_email_sending_domain_does_not_determine_portal_url(self):
        self.org.subdomain = "titanrobotics"
        self.org.settings = {}
        self.org.save(update_fields=["subdomain"])
        custom_cfg = {
            "custom_domain": {
                "enabled": True,
                "domain": "titanrobotics.org",
            }
        }
        update_organization_email_settings(self.org, custom_cfg)
        cfg = get_organization_email_settings(self.org)
        self.assertEqual(cfg["portal_url"], "https://titanrobotics.momentumteam.app")

    def test_base_notification_template_renders_without_link_when_no_portal_url(self):
        cfg = {
            "organization_name": "Test Org",
            "portal_url": "",
        }
        context = {
            "org_email_settings": cfg,
            "organization": self.org,
        }
        rendered = render_to_string("programs/emails/base_notification.html", context)
        self.assertIn(
            "This is an automated notification from the Test Org Portal.", rendered
        )
        self.assertNotIn("Test Org Portal</a>", rendered)

    def test_base_notification_top_letterhead_block(self):
        template_str = (
            '{% extends "programs/emails/base_notification.html" %}'
            '{% block top_letterhead %}<div id="receipt-letterhead">Invoice #12345</div>{% endblock %}'
            "{% block content %}<p>Receipt details here</p>{% endblock %}"
        )
        from django.template import Context, Template

        template = Template(template_str)
        rendered = template.render(
            Context(
                {
                    "org_email_settings": {
                        "organization_name": "Test Org",
                        "portal_url": "https://testorg.example.com",
                    },
                    "organization": self.org,
                }
            )
        )
        self.assertIn('<div id="receipt-letterhead">Invoice #12345</div>', rendered)
        self.assertIn("Receipt details here", rendered)
        self.assertIn(
            'This is an automated notification from the <a href="https://testorg.example.com"',
            rendered,
        )

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
        resp = self.client.get(reverse("portal_settings") + "?tab=branding")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="branding-tab"')
        self.assertContains(resp, 'id="branding"')
        self.assertContains(resp, "Organization Branding")
        self.assertContains(resp, "Primary Brand Color")
        self.assertContains(resp, "Team Icon")
        self.assertContains(resp, "Team Wordmark")
        self.assertContains(resp, "Host Organization Logo")
        self.assertNotContains(resp, 'name="portal_url"')
        self.assertContains(resp, "Email Template Live Preview")

    def test_wrap_email_document_renders_branding(self):
        from programs.utils.email_html import wrap_email_document

        self.org_settings.data = {
            "email_settings": {
                "primary_color": "#112233",
                "organization_name": "Titan Robotics",
                "address_line1": "456 Tech Blvd",
                "contact_email": "info@titanrobotics.org",
                "portal_url": "https://portal.titanrobotics.org",
            }
        }
        self.org_settings.save()

        html = wrap_email_document(
            "<p>Important announcement to the team.</p>",
            organization=self.org,
            subject="Team Announcement",
        )
        self.assertIn("Titan Robotics", html)
        self.assertIn("#112233", html)
        self.assertIn("456 Tech Blvd", html)
        self.assertIn("info@titanrobotics.org", html)
        self.assertIn("Team Announcement", html)
        self.assertIn("Important announcement to the team.", html)
        self.assertIn(
            f'This is an automated notification from the <a href="{get_organization_url(self.org)}"',
            html,
        )

    def test_build_email_parts_renders_branded_html_and_text(self):
        from programs.utils.email_html import build_email_parts

        self.org_settings.data = {
            "email_settings": {
                "organization_name": "Titan Robotics",
                "address_line1": "456 Tech Blvd",
                "portal_url": "https://portal.titanrobotics.org",
            }
        }
        self.org_settings.save()

        inlined_html, text_doc, images = build_email_parts(
            "<p>Welcome to the season kickoff!</p>",
            organization=self.org,
            subject="Season Kickoff",
        )
        self.assertIn("Titan Robotics", inlined_html)
        self.assertIn("456 Tech Blvd", inlined_html)
        self.assertIn("Season Kickoff", inlined_html)
        self.assertIn("Welcome to the season kickoff!", inlined_html)
        self.assertIn("Titan Robotics", text_doc)
        self.assertIn("Welcome to the season kickoff!", text_doc)

    def test_login_code_email_renders_branding(self):
        from allauth.account.adapter import get_adapter

        self.org_settings.data = {
            "email_settings": {
                "primary_color": "#2c3e50",
                "organization_name": "Apex Robotics",
                "portal_url": "https://apex.example.com",
            }
        }
        self.org_settings.save()

        adapter = get_adapter()
        adapter.request = RequestFactory().get("/")
        adapter.request.organization = self.org
        adapter.send_mail(
            "account/email/login_code",
            "student@example.com",
            {"code": "842109"},
        )

        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(len(sent.alternatives), 1)
        html_body, mime = sent.alternatives[0]
        self.assertEqual(mime, "text/html")
        self.assertIn("842109", html_body)
        self.assertIn("Apex Robotics", html_body)
        self.assertIn("#2c3e50", html_body)
        self.assertIn(
            f'This is an automated notification from the <a href="{get_organization_url(self.org)}"',
            html_body,
        )

    def test_guest_form_confirmation_email_renders_branding(self):
        from guest_forms.models import GuestForm, GuestFormSubmission
        from guest_forms.services import send_submission_confirmation_email

        self.org_settings.data = {
            "email_settings": {
                "primary_color": "#008080",
                "organization_name": "Teal Robotics",
                "portal_url": "https://teal.example.com",
            }
        }
        self.org_settings.save()

        gf = GuestForm.objects.create(name="Visitor Waiver", slug="visitor-waiver")
        sub = GuestFormSubmission.objects.create(
            guest_form=gf,
            participant_first_name="Jane",
            participant_last_name="Doe",
            email="visitor@example.com",
        )
        send_submission_confirmation_email(sub)

        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(len(sent.alternatives), 1)
        html_body, mime = sent.alternatives[0]
        self.assertEqual(mime, "text/html")
        self.assertIn("Visitor Waiver", html_body)
        self.assertIn("Jane", html_body)
        self.assertIn("Teal Robotics", html_body)
        self.assertIn(
            f'This is an automated notification from the <a href="{get_organization_url(self.org)}"',
            html_body,
        )
        self.assertIn("Jane Doe", html_body)

    def test_relative_logo_url_resolution(self):
        from django.test import override_settings

        self.org_settings.data = {
            "email_settings": {
                "logo_url": "/media/org_logos/my_logo.png",
            }
        }
        self.org_settings.save()

        with override_settings(SITE_URL="https://portal.girlsofsteel.org"):
            cfg = get_organization_email_settings(self.org)
            self.assertEqual(
                cfg["logo_url"],
                "https://portal.girlsofsteel.org/media/org_logos/my_logo.png",
            )

    def test_footer_renders_sent_date_and_clean_body(self):
        cfg = {
            "organization_name": "Nova Robotics",
        }
        context = {
            "org_email_settings": cfg,
            "organization": self.org,
            "body_content": "<p>Hello team!</p>",
        }
        rendered = render_to_string("programs/emails/base_notification.html", context)
        self.assertIn("Sent on", rendered)
        self.assertIn("Hello team!", rendered)
        # Ensure the old top date bar table is not present
        self.assertNotIn("Date: ", rendered.split("email-main-content")[0])


@override_settings(MOMENTUM_ROOT_DOMAIN="momentumteam.app")
class CanonicalEmailBrandingTests(TestCase):
    @override_settings(DEFAULT_ORGANIZATION_SLUG="missing")
    def test_missing_organization_does_not_link_to_generic_site(self):
        self.assertEqual(get_organization_email_settings()["portal_url"], "")

    def test_active_domain_then_disabled_domain_ignore_all_legacy_overrides(self):
        from django.utils import timezone

        org = Organization.objects.create(
            name="Robots",
            slug="robots",
            subdomain="robots",
            settings={
                "portal_url": "https://wrong.example.org",
                "site_url": "https://other.example.org",
            },
        )
        OrganizationSettings.objects.update_or_create(
            organization=org,
            defaults={
                "data": {
                    "email_settings": {"portal_url": "https://override.example.org"}
                }
            },
        )
        domain = OrganizationDomain.objects.create(
            organization=org,
            hostname="robots.example.org",
            status=OrganizationDomain.STATUS_ACTIVE,
            dns_verified_at=timezone.now(),
            activated_at=timezone.now(),
            activated_by=User.objects.create_user(username="domain_operator"),
        )
        self.assertEqual(
            get_organization_email_settings(org)["portal_url"],
            "https://robots.example.org",
        )
        domain.status = OrganizationDomain.STATUS_DISABLED
        domain.save(update_fields=["status"])
        self.assertEqual(
            get_organization_email_settings(org)["portal_url"],
            "https://robots.momentumteam.app",
        )
        org.is_active = False
        org.save(update_fields=["is_active"])
        self.assertEqual(get_organization_email_settings(org)["portal_url"], "")
