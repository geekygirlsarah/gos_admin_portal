import importlib
from types import SimpleNamespace

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.backends.db import SessionStore
from django.db import connection
from django.db.models.signals import post_init
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils.crypto import get_random_string

from applications.models import Application
from applications.services import send_application_submitted_email
from calendar_feeds.ics import PersonalICSFeedView
from calendar_feeds.models import UserCalendarToken
from guest_forms.models import GuestForm, GuestFormSubmission
from guest_forms.services import send_submission_confirmation_email
from organizations.models import (
    Organization,
    OrganizationSettings,
    UserOrganizationMembership,
)
from organizations.utils import get_default_organization
from programs.models import Adult, Student
from programs.permission_views import get_user_role
from programs.signals import sync_adult_memberships, sync_student_memberships
from programs.views.org_emails import org_email_management_view
from programs.views.portal_settings import PortalEmailSettingsView

User = get_user_model()


class NoGoSDefaultsTests(TestCase):
    def test_empty_default_organization_slug_does_not_resolve_an_organization(self):
        with override_settings(DEFAULT_ORGANIZATION_SLUG=""):
            self.assertIsNone(get_default_organization())

    def test_new_organization_has_neutral_role_and_account_defaults(self):
        organization = Organization.objects.create(
            name="Copper Robotics", slug="copper-robotics"
        )

        self.assertEqual(organization.get_setting("portal_title"), "")
        self.assertFalse(organization.get_setting("org_account_enabled"))
        self.assertEqual(organization.get_setting("org_account_name"), "")
        self.assertEqual(organization.get_setting("org_email_domain"), "")
        self.assertFalse(organization.get_setting("force_mentor_org_email"))
        self.assertEqual(organization.get_setting("clearance_types"), [])
        self.assertFalse(organization.is_org_email("mentor@andrew.cmu.edu"))
        self.assertFalse(organization.is_role_forced_org_email("mentor"))

    def test_legacy_defaults_migration_preserves_existing_gos_values(self):
        organization = Organization.objects.get(slug="gos")
        organization.settings = {
            "portal_title": "Custom GoS Portal",
            "org_account_name": "Custom account",
        }
        organization.save(update_fields=["settings"])
        other_organization = Organization.objects.create(
            name="Copper Robotics", slug="copper-migration"
        )
        email_settings_row, _ = OrganizationSettings.objects.get_or_create(
            organization=organization
        )
        email_settings_row.data = {
            "preserved": "value",
            "email_settings": {"logo_url": "/custom-logo.png"},
        }
        email_settings_row.save(update_fields=["data"])

        migration = importlib.import_module(
            "organizations.migrations.0012_neutral_organization_defaults"
        )
        migration.preserve_legacy_gos_defaults(
            django_apps, SimpleNamespace(connection=connection)
        )
        organization.refresh_from_db()
        email_settings_row.refresh_from_db()

        self.assertEqual(organization.get_setting("portal_title"), "Custom GoS Portal")
        self.assertEqual(organization.get_setting("org_account_name"), "Custom account")
        self.assertEqual(organization.get_setting("org_email_domain"), "andrew.cmu.edu")
        self.assertTrue(organization.get_setting("force_mentor_org_email"))
        self.assertEqual(email_settings_row.data["preserved"], "value")
        self.assertEqual(
            email_settings_row.data["email_settings"]["logo_url"],
            "/custom-logo.png",
        )
        self.assertEqual(
            email_settings_row.data["email_settings"]["contact_email"],
            "leads@girlsofsteelrobotics.org",
        )
        self.assertEqual(dict(other_organization.settings), {})

        data_after_first_run = dict(email_settings_row.data)
        settings_after_first_run = dict(organization.settings)
        migration.preserve_legacy_gos_defaults(
            django_apps, SimpleNamespace(connection=connection)
        )
        organization.refresh_from_db()
        email_settings_row.refresh_from_db()
        self.assertEqual(dict(organization.settings), settings_after_first_run)
        self.assertEqual(email_settings_row.data, data_after_first_run)

    def test_member_without_organization_context_has_no_role(self):
        organization, _ = Organization.objects.get_or_create(
            slug="gos", defaults={"name": "Girls of Steel"}
        )
        member = User.objects.create_user(username="unscoped-member")
        UserOrganizationMembership.objects.create(
            user=member,
            organization=organization,
            role="LeadMentor",
            is_active=True,
        )

        self.assertIsNone(get_user_role(member))

    def test_adult_without_organization_does_not_create_membership(self):
        member = User.objects.create_user(username="unscoped-adult")
        from GoSAdminPortal.test_runner import _assign_default_test_org

        post_init.disconnect(
            receiver=_assign_default_test_org,
            sender=Adult,
            dispatch_uid=f"default-test-org-{Adult._meta.label}",
        )
        self.addCleanup(
            post_init.connect,
            receiver=_assign_default_test_org,
            sender=Adult,
            dispatch_uid=f"default-test-org-{Adult._meta.label}",
        )
        adult = Adult(
            organization=None,
            user=member,
            is_mentor=True,
            mentor_active=True,
        )

        self.assertIsNone(adult.organization_id)
        self.assertEqual(sync_adult_memberships(adult), 0)
        self.assertFalse(
            UserOrganizationMembership.objects.filter(user=member).exists()
        )

    def test_student_without_organization_does_not_create_membership(self):
        member = User.objects.create_user(username="unscoped-student")
        from GoSAdminPortal.test_runner import _assign_default_test_org

        post_init.disconnect(
            receiver=_assign_default_test_org,
            sender=Student,
            dispatch_uid=f"default-test-org-{Student._meta.label}",
        )
        self.addCleanup(
            post_init.connect,
            receiver=_assign_default_test_org,
            sender=Student,
            dispatch_uid=f"default-test-org-{Student._meta.label}",
        )
        student = Student(organization=None, user=member)

        self.assertIsNone(student.organization_id)
        self.assertEqual(sync_student_memberships(student), 0)
        self.assertFalse(
            UserOrganizationMembership.objects.filter(user=member).exists()
        )

    @override_settings(DEFAULT_ORGANIZATION_SLUG="")
    def test_email_settings_without_request_organization_does_not_mutate_tenants(self):
        superuser = User.objects.create_superuser(
            username="platform-admin",
            email="admin@example.test",
            password=get_random_string(32),
        )
        before = list(
            OrganizationSettings.objects.order_by("organization_id").values_list(
                "organization_id", "data"
            )
        )
        request = RequestFactory().post(
            reverse("portal_email_settings"),
            {"action": "update_branding", "organization_name": "Should Not Be Saved"},
        )
        request.user = superuser
        request.organization = None
        request.session = SessionStore()
        request._messages = FallbackStorage(request)

        response = PortalEmailSettingsView.as_view()(request)

        after = list(
            OrganizationSettings.objects.order_by("organization_id").values_list(
                "organization_id", "data"
            )
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(after, before)

    def test_org_account_id_without_configured_domain_does_not_get_andrew_domain(self):
        organization = Organization.objects.create(
            name="Copper Robotics", slug="copper-org-email"
        )
        organization.settings = {
            "org_account_enabled": True,
            "org_account_name": "Organization account",
        }
        organization.save(update_fields=["settings"])
        lead = User.objects.create_user(username="copper-lead")
        UserOrganizationMembership.objects.create(
            user=lead,
            organization=organization,
            role="LeadMentor",
            is_active=True,
        )
        adult = Adult.objects.create(
            organization=organization,
            user=User.objects.create_user(username="copper-adult"),
            legal_first_name="Avery",
            last_name="Robot",
        )
        request = RequestFactory().post(
            "/programs/org-emails/",
            {
                "action": "set",
                "person_type": "adult",
                "person_id": adult.pk,
                "org_account_id": "roboticsid",
            },
        )
        request.user = lead
        request.organization = organization
        request.session = SessionStore()
        request._messages = FallbackStorage(request)

        response = org_email_management_view(request)

        adult.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(adult.org_account_id, "roboticsid")
        self.assertIsNone(adult.org_email)

    def test_guest_form_email_uses_guest_form_organization_branding(self):
        organization = Organization.objects.create(
            name="Copper Robotics", slug="copper-guest-email"
        )
        OrganizationSettings.objects.create(
            organization=organization,
            data={"email_settings": {"organization_name": "Copper Robotics"}},
        )
        guest_form = GuestForm.objects.create(
            organization=organization, name="Photo Release"
        )
        submission = GuestFormSubmission.objects.create(
            organization=organization,
            guest_form=guest_form,
            participant_first_name="Avery",
            email="applicant@example.test",
        )

        send_submission_confirmation_email(submission)

        from django.core import mail

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Copper Robotics", mail.outbox[0].body)
        self.assertNotIn("Girls of Steel", mail.outbox[0].body)

    def test_application_submission_email_subject_uses_organization_name(self):
        organization = Organization.objects.create(
            name="Copper Robotics", slug="copper-application-email"
        )
        application = Application.objects.create(
            organization=organization,
            email="applicant@example.test",
            applicant_type=Application.Type.PARENT,
            data={"step5-student": {"email": "applicant@example.test"}},
        )

        send_application_submitted_email(application)

        from django.core import mail

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Copper Robotics", mail.outbox[0].subject)
        self.assertNotIn("Girls of Steel", mail.outbox[0].subject)

    def test_personal_ics_feed_uses_organization_name_and_neutral_prodid(self):
        organization = Organization.objects.create(
            name="Copper Robotics", slug="copper-calendar"
        )
        user = User.objects.create_superuser(
            username="calendar-admin",
            email="calendar@example.test",
            password=get_random_string(32),
        )
        token = UserCalendarToken.objects.create(user=user)
        request = RequestFactory().get("/")
        request.organization = organization

        response = PersonalICSFeedView.as_view()(request, token=str(token.token))
        body = response.content.decode("utf-8")

        self.assertIn("X-WR-CALNAME:Copper Robotics Calendar", body)
        self.assertNotIn("GoS Admin Portal", body)

    def test_tenant_login_page_uses_its_name_without_gos_branding(self):
        Organization.objects.create(
            name="Copper Robotics", slug="copper-login", subdomain="copper-login"
        )

        response = Client().get(
            reverse("account_login"), HTTP_HOST="copper-login.localhost"
        )
        body = response.content.decode("utf-8")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Copper Robotics", body)
        self.assertNotIn("Girls of Steel", body)
        self.assertNotIn("girlsofsteelrobotics.org", body)
