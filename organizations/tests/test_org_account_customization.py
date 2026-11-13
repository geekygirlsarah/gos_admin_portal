from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse

from GoSAdminPortal.adapter import _find_or_provision_user_for_email
from GoSAdminPortal.context_processors import organization_context
from organizations.models import Organization, UserOrganizationMembership
from organizations.platform_forms import PlatformOrganizationForm
from organizations.utils import (
    get_default_organization,
    update_organization_settings,
)
from programs.models import Adult, Student

User = get_user_model()


class OrganizationAccountModelMethodTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            name="Sciencey Robotics", slug="sciencey"
        )

    def test_default_organization_settings(self):
        self.assertFalse(self.org.get_setting("org_account_enabled"))
        self.assertEqual(self.org.get_setting("org_account_name"), "")
        self.assertEqual(self.org.get_setting("org_email_domain"), "")
        self.assertFalse(self.org.get_setting("force_mentor_org_email"))
        self.assertFalse(self.org.get_setting("force_student_org_email"))
        self.assertFalse(self.org.is_org_email("mentor@andrew.cmu.edu"))

    def test_is_org_email_matching(self):
        self.org.set_setting("org_account_enabled", True)
        self.org.set_setting(
            "org_email_domain", "scienceyrobotics.org, somewhere.pa.k12.us"
        )
        self.assertTrue(self.org.is_org_email("mentor@scienceyrobotics.org"))
        self.assertTrue(self.org.is_org_email("STUDENT@somewhere.pa.k12.us"))
        self.assertTrue(self.org.is_org_email("sub@dept.scienceyrobotics.org"))
        self.assertFalse(self.org.is_org_email("user@gmail.com"))
        self.assertFalse(self.org.is_org_email("invalid-email"))
        self.assertFalse(self.org.is_org_email(""))

    def test_is_org_email_disabled(self):
        self.org.set_setting("org_account_enabled", False)
        self.org.set_setting("org_email_domain", "scienceyrobotics.org")
        self.assertFalse(self.org.is_org_email("mentor@scienceyrobotics.org"))

    def test_is_role_forced_org_email(self):
        self.org.set_setting("org_account_enabled", True)
        self.org.set_setting("org_email_domain", "scienceyrobotics.org")
        self.org.set_setting("force_mentor_org_email", True)
        self.org.set_setting("force_student_org_email", True)
        self.org.set_setting("force_parent_org_email", False)
        self.org.set_setting("force_alumni_org_email", False)

        self.assertTrue(self.org.is_role_forced_org_email("mentor"))
        self.assertTrue(self.org.is_role_forced_org_email("lead_mentor"))
        self.assertTrue(self.org.is_role_forced_org_email("student"))
        self.assertFalse(self.org.is_role_forced_org_email("parent"))
        self.assertFalse(self.org.is_role_forced_org_email("alumni"))

    def test_update_organization_settings_utility(self):
        update_organization_settings(
            self.org,
            {
                "org_account_name": "Sciencey SSO",
                "org_email_domain": "sciencey.org",
                "force_student_org_email": True,
            },
        )
        self.org.refresh_from_db()
        self.assertEqual(self.org.get_setting("org_account_name"), "Sciencey SSO")
        self.assertEqual(self.org.get_setting("org_email_domain"), "sciencey.org")
        self.assertTrue(self.org.get_setting("force_student_org_email"))


class MultiTenantLoginAdapterTests(TestCase):
    def setUp(self):
        self.custom_org = Organization.objects.create(
            name="Somewhere High School",
            slug="somewhere-hs",
            settings={
                "org_account_enabled": True,
                "org_account_name": "School Account",
                "org_email_domain": "somewhere.pa.k12.us",
                "force_mentor_org_email": True,
                "force_student_org_email": False,
                "force_parent_org_email": False,
                "force_alumni_org_email": False,
            },
        )

    def test_mentor_forced_to_use_custom_org_email(self):
        mentor = Adult.objects.create(
            legal_first_name="Jane",
            last_name="Doe",
            personal_email="jane.doe@gmail.com",
            andrew_email="jdoe@somewhere.pa.k12.us",
            is_mentor=True,
            organization=self.custom_org,
        )

        # Personal email login denied
        self.assertFalse(_find_or_provision_user_for_email("jane.doe@gmail.com"))

        # Org domain email login allowed
        self.assertTrue(_find_or_provision_user_for_email("jdoe@somewhere.pa.k12.us"))

    def test_mentor_unforced_can_use_personal_or_org_email(self):
        self.custom_org.set_setting("force_mentor_org_email", False)
        mentor = Adult.objects.create(
            legal_first_name="Bob",
            last_name="Smith",
            personal_email="bob.smith@gmail.com",
            andrew_email="bsmith@somewhere.pa.k12.us",
            is_mentor=True,
            organization=self.custom_org,
        )

        # Both allowed
        self.assertTrue(_find_or_provision_user_for_email("bob.smith@gmail.com"))
        self.assertTrue(_find_or_provision_user_for_email("bsmith@somewhere.pa.k12.us"))

    def test_student_optional_org_email_by_default(self):
        student = Student.objects.create(
            legal_first_name="Alice",
            last_name="Johnson",
            personal_email="alice.j@gmail.com",
            andrew_email="ajohnson@somewhere.pa.k12.us",
            organization=self.custom_org,
        )

        # Both allowed by default
        self.assertTrue(_find_or_provision_user_for_email("alice.j@gmail.com"))
        self.assertTrue(
            _find_or_provision_user_for_email("ajohnson@somewhere.pa.k12.us")
        )

    def test_student_forced_to_use_org_email(self):
        self.custom_org.set_setting("force_student_org_email", True)
        student = Student.objects.create(
            legal_first_name="Charlie",
            last_name="Brown",
            personal_email="charlie@gmail.com",
            andrew_email="cbrown@somewhere.pa.k12.us",
            organization=self.custom_org,
        )

        # Personal email denied
        self.assertFalse(_find_or_provision_user_for_email("charlie@gmail.com"))

        # Org domain email allowed
        self.assertTrue(_find_or_provision_user_for_email("cbrown@somewhere.pa.k12.us"))

    def test_personal_only_organization_allows_personal_email_for_all(self):
        personal_org = Organization.objects.create(
            name="Community Robotics",
            slug="community-robotics",
            settings={
                "org_account_enabled": False,
                "org_email_domain": "",
                "force_mentor_org_email": False,
                "force_student_org_email": False,
            },
        )
        mentor = Adult.objects.create(
            legal_first_name="David",
            last_name="Lee",
            personal_email="david.lee@gmail.com",
            is_mentor=True,
            organization=personal_org,
        )
        self.assertTrue(_find_or_provision_user_for_email("david.lee@gmail.com"))


class PortalOrgAccountSettingsViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.org = Organization.objects.create(
            name="Apex Robotics", slug="apex", subdomain="apex"
        )
        self.lead_group, _ = Group.objects.get_or_create(name="Lead Mentors")

        self.lead_mentor_user = User.objects.create_user(
            username="leadmentor@apex.org",
            email="leadmentor@apex.org",
        )
        self.lead_mentor_user.groups.add(self.lead_group)
        UserOrganizationMembership.objects.create(
            user=self.lead_mentor_user,
            organization=self.org,
            role="LeadMentor",
            is_active=True,
        )

        self.regular_user = User.objects.create_user(
            username="student@apex.org",
            email="student@apex.org",
        )

    def test_lead_mentor_can_update_org_account_settings(self):
        self.client.force_login(self.lead_mentor_user)
        response = self.client.post(
            reverse("portal_org_account_settings"),
            {
                "org_account_enabled": "on",
                "org_account_name": "Apex SSO",
                "org_email_domain": "apexrobotics.org",
                "force_mentor_org_email": "on",
                "force_student_org_email": "on",
                "force_parent_org_email": "",
                "force_alumni_org_email": "",
            },
            HTTP_HOST="apex.localhost",
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.org.refresh_from_db()
        self.assertTrue(self.org.get_setting("org_account_enabled"))
        self.assertEqual(self.org.get_setting("org_account_name"), "Apex SSO")
        self.assertEqual(self.org.get_setting("org_email_domain"), "apexrobotics.org")
        self.assertTrue(self.org.get_setting("force_mentor_org_email"))
        self.assertTrue(self.org.get_setting("force_student_org_email"))
        self.assertFalse(self.org.get_setting("force_parent_org_email"))

    def test_regular_user_denied_access(self):
        self.client.force_login(self.regular_user)
        response = self.client.post(
            reverse("portal_org_account_settings"),
            {
                "org_account_name": "Hacked SSO",
            },
            HTTP_HOST="apex.localhost",
        )
        # Should be redirected or denied by LeadMentorRequiredMixin
        self.assertIn(response.status_code, [302, 403])


class PlatformOrganizationFormAccountSettingsTests(TestCase):
    def test_platform_admin_form_saves_account_settings(self):
        form_data = {
            "name": "Sciencey Robotics",
            "slug": "sciencey-team",
            "subdomain": "sciencey",
            "is_active": True,
            "time_zone": "America/New_York",
            "theme_color": "#0056b3",
            "org_account_enabled": True,
            "org_account_name": "Sciencey ID",
            "org_email_domain": "scienceyrobotics.org",
            "force_mentor_org_email": True,
            "force_student_org_email": True,
            "force_parent_org_email": False,
            "force_alumni_org_email": False,
        }
        form = PlatformOrganizationForm(data=form_data)
        self.assertTrue(form.is_valid(), form.errors)
        org = form.save()

        self.assertEqual(org.get_setting("org_account_name"), "Sciencey ID")
        self.assertEqual(org.get_setting("org_email_domain"), "scienceyrobotics.org")
        self.assertTrue(org.get_setting("force_mentor_org_email"))
        self.assertTrue(org.get_setting("force_student_org_email"))


class AndrewIdManagementViewTenantTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.org = Organization.objects.create(
            name="Somewhere HS",
            slug="somewhere-hs",
            subdomain="somewhere",
            settings={
                "org_account_name": "School ID",
                "org_email_domain": "somewhere.pa.k12.us",
            },
        )
        self.lead_group, _ = Group.objects.get_or_create(name="Lead Mentors")
        self.lead_user = User.objects.create_user(
            username="lead@somewhere.org",
            email="lead@somewhere.org",
        )
        self.lead_user.groups.add(self.lead_group)
        UserOrganizationMembership.objects.create(
            user=self.lead_user,
            organization=self.org,
            role="LeadMentor",
            is_active=True,
        )
        self.student = Student.objects.create(
            legal_first_name="Sara",
            last_name="Connor",
            personal_email="sara@gmail.com",
            organization=self.org,
        )

    def test_assign_account_uses_custom_org_domain(self):
        self.client.force_login(self.lead_user)
        response = self.client.post(
            reverse("andrew_id_management"),
            {
                "action": "set",
                "person_type": "student",
                "person_id": self.student.pk,
                "andrew_id": "sconnor",
            },
            HTTP_HOST="somewhere-hs.localhost",
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.student.refresh_from_db()
        self.assertEqual(self.student.andrew_id, "sconnor")
        self.assertEqual(self.student.andrew_email, "sconnor@somewhere.pa.k12.us")


class OrganizationContextProcessorAndLoginTemplateTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_context_processor_returns_org_account_settings(self):
        factory = RequestFactory()
        request = factory.get("/")
        ctx = organization_context(request)
        self.assertIn("org_account_enabled", ctx)
        self.assertIn("org_account_name", ctx)
        self.assertIn("org_email_domain", ctx)
        self.assertIn("force_mentor_org_email", ctx)
        self.assertIn("force_student_org_email", ctx)

    def test_login_page_renders_custom_login_hint(self):
        org = Organization.objects.create(
            name="Sciencey Org",
            slug="sciencey-org",
            subdomain="sciencey",
            settings={
                "org_account_enabled": True,
                "org_account_name": "Sciencey SSO",
                "org_email_domain": "scienceyrobotics.org",
                "force_mentor_org_email": True,
                "force_student_org_email": True,
            },
        )
        response = self.client.get("/accounts/login/", HTTP_HOST="sciencey.localhost")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "@scienceyrobotics.org")
        self.assertContains(response, "Sciencey SSO")
