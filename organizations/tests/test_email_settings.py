import json

from django.conf import settings
from django.contrib.auth.models import Group, User
from django.test import TestCase, override_settings
from django.urls import reverse

from organizations.models import Organization, OrganizationSettings
from organizations.utils import (
    get_organization_email_settings,
    get_sender_accounts,
    update_organization_email_settings,
)
from programs.forms import ProgramEmailBalancesForm, ProgramEmailForm
from programs.models import Adult, Program
from programs.utils.notifications import get_sender_connection


class OrganizationEmailSettingsUtilsTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Test Robotics", slug="test-org")
        self.org_settings = OrganizationSettings.objects.create(organization=self.org)

    def test_get_organization_email_settings_defaults(self):
        with override_settings(
            DEFAULT_FROM_NAME="Platform Default",
            DEFAULT_REPLY_TO_EMAIL="platform-reply@example.com",
            NOTIFICATION_FROM_EMAIL="notifications@platform.example.com",
        ):
            email_cfg = get_organization_email_settings(self.org)
            self.assertEqual(email_cfg["sender_display_name"], "Test Robotics")
            self.assertEqual(email_cfg["reply_to_email"], "platform-reply@example.com")
            self.assertEqual(
                email_cfg["notification_from_email"],
                "notifications@platform.example.com",
            )
            self.assertEqual(email_cfg["sender_accounts"], [])

    def test_get_organization_email_settings_customized(self):
        self.org_settings.data = {
            "email_settings": {
                "sender_display_name": "Team 1234",
                "reply_to_email": "leads@team1234.org",
                "notification_from_email": "notifications@team1234.org",
                "sender_accounts": [
                    {
                        "key": "leads",
                        "display_name": "Team 1234 Leads",
                        "email": "leads@team1234.org",
                        "reply_to": "leads@team1234.org",
                    }
                ],
                "custom_domain": {
                    "enabled": True,
                    "domain": "team1234.org",
                    "from_email": "notifications@team1234.org",
                },
            }
        }
        self.org_settings.save()

        email_cfg = get_organization_email_settings(self.org)
        self.assertEqual(email_cfg["sender_display_name"], "Team 1234")
        self.assertEqual(email_cfg["reply_to_email"], "leads@team1234.org")
        self.assertEqual(
            email_cfg["notification_from_email"], "notifications@team1234.org"
        )
        self.assertEqual(len(email_cfg["sender_accounts"]), 1)
        self.assertEqual(email_cfg["sender_accounts"][0]["key"], "leads")
        self.assertTrue(email_cfg["custom_domain"]["enabled"])

    def test_get_sender_accounts_fallback_to_settings(self):
        with override_settings(
            EMAIL_SENDER_ACCOUNTS=[
                {
                    "key": "global_leads",
                    "display_name": "Global Leads",
                    "email": "global@example.com",
                    "reply_to": "global@example.com",
                }
            ]
        ):
            # No custom accounts on org -> falls back to settings.EMAIL_SENDER_ACCOUNTS
            accounts = get_sender_accounts(self.org)
            self.assertEqual(len(accounts), 1)
            self.assertEqual(accounts[0]["key"], "global_leads")

    def test_get_sender_accounts_uses_org_accounts_when_configured(self):
        self.org_settings.data = {
            "email_settings": {
                "sender_accounts": [
                    {
                        "key": "custom_acc",
                        "display_name": "Custom Identity",
                        "email": "custom@example.com",
                        "reply_to": "custom@example.com",
                    }
                ]
            }
        }
        self.org_settings.save()

        accounts = get_sender_accounts(self.org)
        self.assertEqual(len(accounts), 1)
        self.assertEqual(accounts[0]["key"], "custom_acc")

    def test_get_sender_connection_with_org_accounts(self):
        self.org_settings.data = {
            "email_settings": {
                "sender_display_name": "Org Brand",
                "reply_to_email": "org-default-reply@example.com",
                "notification_from_email": "notifications@orgbrand.com",
                "sender_accounts": [
                    {
                        "key": "mentors",
                        "display_name": "Org Mentors",
                        "email": "mentors@orgbrand.com",
                        "reply_to": "mentors-reply@orgbrand.com",
                    }
                ],
            }
        }
        self.org_settings.save()

        # Selected custom account
        conn, from_email, reply_to = get_sender_connection(
            "mentors", organization=self.org
        )
        self.assertEqual(from_email, '"Org Mentors" <mentors@orgbrand.com>')
        self.assertEqual(reply_to, "mentors-reply@orgbrand.com")

        # Default connection
        conn, from_email, reply_to = get_sender_connection(
            "DEFAULT", organization=self.org
        )
        self.assertEqual(from_email, '"Org Brand" <notifications@orgbrand.com>')
        self.assertEqual(reply_to, "org-default-reply@example.com")


class PortalEmailSettingsViewTests(TestCase):
    def setUp(self):
        self.password = "password123"  # nosec B105 B106
        self.lead_user = User.objects.create_user(
            username="lead_user", password=self.password
        )
        self.lead_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.lead_user.groups.add(self.lead_group)

        self.mentor_user = User.objects.create_user(
            username="regular_mentor", password=self.password
        )
        self.mentor_group, _ = Group.objects.get_or_create(name="Mentor")
        self.mentor_user.groups.add(self.mentor_group)
        Adult.objects.create(
            user=self.mentor_user,
            legal_first_name="Regular",
            last_name="Mentor",
            is_mentor=True,
        )

        self.org, _ = Organization.objects.get_or_create(
            slug="gos", defaults={"name": "Girls of Steel"}
        )
        self.org_settings, _ = OrganizationSettings.objects.get_or_create(
            organization=self.org
        )
        self.url = reverse("portal_email_settings")

    def test_non_lead_mentor_denied(self):
        self.client.login(username="regular_mentor", password=self.password)
        resp = self.client.post(
            self.url,
            {
                "action": "update_email_settings",
                "sender_display_name": "Hacked",
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("home"))

    def test_lead_mentor_updates_general_email_settings(self):
        self.client.login(username="lead_user", password=self.password)
        resp = self.client.post(
            self.url,
            {
                "action": "update_email_settings",
                "sender_display_name": "New Team Name",
                "reply_to_email": "new-leads@example.com",
                "notification_from_email": "notifications@customdomain.org",
                "custom_domain_enabled": "on",
                "custom_domain_domain": "customdomain.org",
                "custom_domain_from_email": "notifications@customdomain.org",
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("tab=email", resp.url)

        self.org_settings.refresh_from_db()
        email_cfg = self.org_settings.data.get("email_settings", {})
        self.assertEqual(email_cfg["sender_display_name"], "New Team Name")
        self.assertEqual(email_cfg["reply_to_email"], "new-leads@example.com")
        self.assertEqual(
            email_cfg["notification_from_email"], "notifications@customdomain.org"
        )
        self.assertTrue(email_cfg["custom_domain"]["enabled"])
        self.assertEqual(email_cfg["custom_domain"]["domain"], "customdomain.org")

    def test_lead_mentor_crud_sender_accounts(self):
        self.client.login(username="lead_user", password=self.password)

        # 1. Add sender account
        resp = self.client.post(
            self.url,
            {
                "action": "add_sender_account",
                "key": "outreach",
                "display_name": "GoS Outreach",
                "email": "outreach@girlsofsteelrobotics.org",
                "reply_to": "outreach-team@girlsofsteelrobotics.org",
            },
        )
        self.assertEqual(resp.status_code, 302)

        self.org_settings.refresh_from_db()
        accounts = self.org_settings.data.get("email_settings", {}).get(
            "sender_accounts", []
        )
        self.assertEqual(len(accounts), 1)
        self.assertEqual(accounts[0]["key"], "outreach")
        self.assertEqual(accounts[0]["display_name"], "GoS Outreach")

        # 2. Add duplicate key rejected
        resp = self.client.post(
            self.url,
            {
                "action": "add_sender_account",
                "key": "outreach",
                "display_name": "Duplicate Outreach",
                "email": "outreach2@girlsofsteelrobotics.org",
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.org_settings.refresh_from_db()
        accounts = self.org_settings.data.get("email_settings", {}).get(
            "sender_accounts", []
        )
        self.assertEqual(len(accounts), 1)

        # 3. Update sender account
        resp = self.client.post(
            self.url,
            {
                "action": "update_sender_account",
                "key": "outreach",
                "display_name": "GoS Outreach Team",
                "email": "outreach-updated@girlsofsteelrobotics.org",
                "reply_to": "outreach-reply@girlsofsteelrobotics.org",
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.org_settings.refresh_from_db()
        accounts = self.org_settings.data.get("email_settings", {}).get(
            "sender_accounts", []
        )
        self.assertEqual(accounts[0]["display_name"], "GoS Outreach Team")
        self.assertEqual(
            accounts[0]["email"], "outreach-updated@girlsofsteelrobotics.org"
        )

        # 4. Delete sender account
        resp = self.client.post(
            self.url,
            {
                "action": "delete_sender_account",
                "key": "outreach",
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.org_settings.refresh_from_db()
        accounts = self.org_settings.data.get("email_settings", {}).get(
            "sender_accounts", []
        )
        self.assertEqual(len(accounts), 0)

    def test_portal_settings_page_renders_email_tab(self):
        self.client.login(username="lead_user", password=self.password)
        resp = self.client.get(reverse("portal_settings") + "?tab=email")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Email Settings")
        self.assertContains(resp, "Sender Identities")
        self.assertContains(resp, "Reply-To")
        # Check for user-friendly explanation of default sending and configurable settings
        self.assertContains(
            resp,
            "By default, messages will be sent from the system's email address, but you can update that with the settings below.",
        )
        # Check for custom domain setup explanation
        self.assertContains(resp, "How Custom Domain Setup Works")
        self.assertContains(
            resp, "You do not need to change your MX records or mailboxes"
        )
        self.assertContains(resp, "DNS verification records")
        # Ensure technical jargon and references to toSend or SMTP are removed from the settings UI
        content = resp.content.decode("utf-8")
        self.assertNotIn("toSend", content)
        self.assertNotIn("SMTP", content)
        self.assertNotIn("SPF/DKIM/DMARC", content)

    def test_forms_use_organization_sender_accounts(self):
        from applications.views.review import ApplicationEmailForm

        self.org_settings.data = {
            "email_settings": {
                "sender_accounts": [
                    {
                        "key": "finance",
                        "display_name": "GoS Finance",
                        "email": "finance@girlsofsteelrobotics.org",
                        "reply_to": "finance-reply@girlsofsteelrobotics.org",
                    }
                ]
            }
        }
        self.org_settings.save()

        program = Program.objects.create(name="FRC 2026", active=True)

        prog_form = ProgramEmailForm(program=program)
        self.assertIn(
            "finance", [c[0] for c in prog_form.fields["from_account"].choices]
        )
        self.assertEqual(prog_form.fields["from_account"].initial, "finance")

        balances_form = ProgramEmailBalancesForm(program=program)
        self.assertIn(
            "finance", [c[0] for c in balances_form.fields["from_account"].choices]
        )
        self.assertEqual(balances_form.fields["from_account"].initial, "finance")

        app_form = ApplicationEmailForm()
        self.assertIn(
            "finance", [c[0] for c in app_form.fields["from_account"].choices]
        )
        self.assertEqual(app_form.fields["from_account"].initial, "finance")

    def test_add_sender_account_missing_fields_rejected(self):
        self.client.login(username="lead_user", password=self.password)
        resp = self.client.post(
            self.url,
            {
                "action": "add_sender_account",
                "key": "",
                "email": "",
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.org_settings.refresh_from_db()
        accounts = self.org_settings.data.get("email_settings", {}).get(
            "sender_accounts", []
        )
        self.assertEqual(len(accounts), 0)
