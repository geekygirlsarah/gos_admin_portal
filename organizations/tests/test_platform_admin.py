"""Tests for Platform Administration Dashboard, Org Health, Metrics, Impersonation, and Support Tools."""

from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from audit.events import AuditEvent
from audit.models import AuditLog
from organizations.models import (
    Organization,
    OrganizationInvitation,
    OrganizationSettings,
    UserOrganizationMembership,
)
from programs.models import (
    Adult,
    BackgroundCheck,
    BackgroundCheckType,
    Enrollment,
    MentorAgreement,
    MentorAgreementAcceptance,
    Program,
    Student,
)


class PlatformAdminAccessControlTests(TestCase):
    """Test superuser access restrictions across all platform admin endpoints."""

    def setUp(self):
        self.superuser = User.objects.create_superuser(
            username="admin", email="admin@example.com"
        )
        self.lead_mentor = User.objects.create_user(
            username="lead_mentor", email="lead@example.com"
        )
        self.regular_user = User.objects.create_user(
            username="student_user", email="student@example.com"
        )
        self.org = Organization.objects.create(name="Steel City", slug="steel-city")

    def test_anonymous_redirected_to_login(self):
        """Anonymous requests to platform admin views must redirect to login."""
        urls = [
            reverse("platform_admin:dashboard"),
            reverse("platform_admin:organization_list"),
            reverse("platform_admin:organization_create"),
            reverse("platform_admin:organization_detail", kwargs={"pk": self.org.pk}),
            reverse("platform_admin:metrics"),
            reverse("platform_admin:health"),
            reverse("platform_admin:user_support"),
            reverse("platform_admin:audit_logs"),
        ]
        for url in urls:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302, f"Failed for {url}")
            self.assertIn("/accounts/login/", response.url)

    def test_non_superuser_denied_access(self):
        """Authenticated non-superusers (including Lead Mentors) must receive 403 Forbidden."""
        self.client.force_login(self.lead_mentor)
        urls = [
            reverse("platform_admin:dashboard"),
            reverse("platform_admin:organization_list"),
            reverse("platform_admin:organization_create"),
            reverse("platform_admin:organization_detail", kwargs={"pk": self.org.pk}),
            reverse("platform_admin:metrics"),
            reverse("platform_admin:health"),
            reverse("platform_admin:user_support"),
            reverse("platform_admin:audit_logs"),
        ]
        for url in urls:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 403, f"Non-superuser accessed {url}")

    def test_superuser_allowed_access(self):
        """Active superusers can access all platform admin views."""
        self.client.force_login(self.superuser)
        urls = [
            reverse("platform_admin:dashboard"),
            reverse("platform_admin:organization_list"),
            reverse("platform_admin:organization_create"),
            reverse("platform_admin:organization_detail", kwargs={"pk": self.org.pk}),
            reverse("platform_admin:metrics"),
            reverse("platform_admin:health"),
            reverse("platform_admin:user_support"),
            reverse("platform_admin:audit_logs"),
        ]
        for url in urls:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, f"Superuser denied for {url}")


class PlatformDashboardAndHealthTests(TestCase):
    """Test dashboard metrics calculation and isolation health checks."""

    def setUp(self):
        self.superuser = User.objects.create_superuser(
            username="admin", email="admin@example.com"
        )
        self.client.force_login(self.superuser)

        self.org1 = Organization.objects.create(
            name="Alpha Org", slug="alpha", is_active=True
        )
        self.org2 = Organization.objects.create(
            name="Beta Org", slug="beta", is_active=False
        )

        # Create members for org1
        self.lead = User.objects.create_user(
            username="alpha_lead", email="alpha_lead@example.com"
        )
        UserOrganizationMembership.objects.create(
            user=self.lead, organization=self.org1, role="LeadMentor", is_active=True
        )
        self.lead_adult = Adult.objects.create(
            user=self.lead,
            legal_first_name="Alpha",
            last_name="Lead",
            is_mentor=True,
            organization=self.org1,
        )
        self.mentor = User.objects.create_user(
            username="alpha_mentor", email="alpha_mentor@example.com"
        )
        UserOrganizationMembership.objects.create(
            user=self.mentor, organization=self.org1, role="Mentor", is_active=True
        )
        self.mentor_adult = Adult.objects.create(
            user=self.mentor,
            legal_first_name="Alpha",
            last_name="Mentor",
            is_mentor=True,
            organization=self.org1,
        )

        # Programs
        self.program = Program.objects.create(
            name="Alpha FRC", organization=self.org1, active=True
        )

    def test_dashboard_context(self):
        """Dashboard renders executive totals and organization health diagnostics."""
        response = self.client.get(reverse("platform_admin:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(response.context["total_orgs"], 2)
        self.assertGreaterEqual(response.context["active_orgs"], 1)
        self.assertEqual(response.context["inactive_orgs"], 1)
        self.assertGreaterEqual(response.context["total_users"], 3)

        health_data = response.context["org_health_data"]
        alpha_health = next(h for h in health_data if h["org"] == self.org1)
        self.assertEqual(alpha_health["lead_mentors_count"], 1)
        self.assertEqual(alpha_health["active_programs_count"], 1)

        beta_health = next(h for h in health_data if h["org"] == self.org2)
        self.assertEqual(beta_health["lead_mentors_count"], 0)
        self.assertEqual(beta_health["status"], "danger")

    def test_organization_list_health_and_compliance(self):
        """Organization list view calculates health diagnostics with agreements and clearances."""
        # Create an active agreement
        agreement = MentorAgreement.objects.create(
            title="Code of Conduct",
            slug="code-of-conduct",
            version=1,
            is_active=True,
            effective_date=timezone.now().date(),
        )

        # Before acceptance: missing agreements issue flagged
        response = self.client.get(reverse("platform_admin:organization_list"))
        self.assertEqual(response.status_code, 200)
        org_items = response.context["org_items"]
        alpha_item = next(item for item in org_items if item["org"] == self.org1)
        self.assertEqual(alpha_item["health"]["agreed_mentors_count"], 0)
        self.assertEqual(alpha_item["health"]["clearances_count"], 0)
        self.assertIn(
            "2 mentor(s) missing accepted agreements", alpha_item["health"]["issues"]
        )

        # Accept agreement for lead mentor
        MentorAgreementAcceptance.objects.create(
            adult=self.lead_adult, agreement=agreement
        )

        # Add background check for mentor
        bct = BackgroundCheckType.objects.create(
            name="PA State Police",
            organization=self.org1,
            applies_to_mentors=True,
            validity_years=5,
        )
        BackgroundCheck.objects.create(
            adult=self.mentor_adult,
            check_type=bct,
            cleared=True,
            obtained_date=timezone.now().date(),
            organization=self.org1,
        )

        response = self.client.get(reverse("platform_admin:organization_list"))
        self.assertEqual(response.status_code, 200)
        alpha_item = next(
            item for item in response.context["org_items"] if item["org"] == self.org1
        )
        self.assertEqual(alpha_item["health"]["agreed_mentors_count"], 1)
        self.assertEqual(alpha_item["health"]["clearances_count"], 1)

    def test_health_check_view_and_isolation_audit(self):
        """Health diagnostics detects isolation status and compliance metrics."""
        response = self.client.get(reverse("platform_admin:health"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["db_ok"])
        self.assertFalse(response.context["has_isolation_issues"])

        # Create an unscoped program to trigger isolation warning
        unscoped_prog = Program.objects.create(name="Unscoped", organization=None)
        response_unscoped = self.client.get(reverse("platform_admin:health"))
        self.assertTrue(response_unscoped.context["has_isolation_issues"])


class PlatformOrganizationManagementTests(TestCase):
    """Test creating, editing, and managing organizations and members."""

    def setUp(self):
        self.superuser = User.objects.create_superuser(
            username="admin", email="admin@example.com"
        )
        self.client.force_login(self.superuser)

    def test_create_organization_with_settings(self):
        """Creating an organization automatically creates its OrganizationSettings and logs audit event."""
        url = reverse("platform_admin:organization_create")
        data = {
            "name": "Robo Knights",
            "slug": "robo-knights",
            "subdomain": "roboknights",
            "is_active": True,
            "portal_title": "Robo Knights Portal",
            "contact_email": "team@roboknights.org",
            "time_zone": "America/New_York",
            "theme_color": "#ff0000",
            "allowed_domains": "roboknights.org",
        }
        response = self.client.post(url, data)
        org = Organization.objects.filter(slug="robo-knights").first()
        self.assertIsNotNone(org)
        self.assertRedirects(
            response,
            reverse("platform_admin:organization_detail", kwargs={"pk": org.pk}),
        )

        # Verify settings
        self.assertEqual(org.settings.portal_title, "Robo Knights Portal")
        self.assertEqual(org.settings.contact_email, "team@roboknights.org")

        # Verify audit log
        self.assertTrue(
            AuditLog.objects.filter(
                event=AuditEvent.ORGANIZATION_CREATED,
                actor=self.superuser,
            ).exists()
        )

    def test_create_organization_with_initial_lead_mentor(self):
        """Creating an organization with existing user email assigns Lead Mentor role immediately."""
        mentor_user = User.objects.create_user(
            username="lead_dev", email="leaddev@example.com"
        )
        data = {
            "name": "Cyber Hawks",
            "slug": "cyber-hawks",
            "is_active": True,
            "initial_lead_mentor_email": "leaddev@example.com",
            "time_zone": "America/New_York",
            "theme_color": "#0056b3",
        }
        self.client.post(reverse("platform_admin:organization_create"), data)
        org = Organization.objects.get(slug="cyber-hawks")

        membership = UserOrganizationMembership.objects.filter(
            organization=org, user=mentor_user
        ).first()
        self.assertIsNotNone(membership)
        self.assertEqual(membership.role, "LeadMentor")

    def test_create_organization_with_unregistered_lead_mentor_invites(self):
        """Creating an organization with unknown email generates an invitation."""
        data = {
            "name": "Quantum Bots",
            "slug": "quantum-bots",
            "is_active": True,
            "initial_lead_mentor_email": "future_lead@example.com",
            "time_zone": "America/New_York",
            "theme_color": "#0056b3",
        }
        self.client.post(reverse("platform_admin:organization_create"), data)
        org = Organization.objects.get(slug="quantum-bots")

        inv = OrganizationInvitation.objects.filter(
            organization=org, email="future_lead@example.com"
        ).first()
        self.assertIsNotNone(inv)
        self.assertEqual(inv.role, "LeadMentor")

    def test_toggle_organization_status(self):
        """Toggling organization active status updates flag and logs audit event."""
        org = Organization.objects.create(
            name="Delta Team", slug="delta", is_active=True
        )
        url = reverse(
            "platform_admin:organization_toggle_status", kwargs={"pk": org.pk}
        )

        self.client.post(url)
        org.refresh_from_db()
        self.assertFalse(org.is_active)

        self.assertTrue(
            AuditLog.objects.filter(
                event=AuditEvent.ORGANIZATION_STATUS_CHANGED,
                actor=self.superuser,
            ).exists()
        )

        # Toggle back to active
        self.client.post(url)
        org.refresh_from_db()
        self.assertTrue(org.is_active)

    def test_manage_organization_members(self):
        """Add, edit, and remove members in an organization."""
        org = Organization.objects.create(name="Echo Team", slug="echo")
        target_user = User.objects.create_user(
            username="member1", email="member1@example.com"
        )

        # 1. Add member
        add_url = reverse(
            "platform_admin:organization_member_add", kwargs={"pk": org.pk}
        )
        self.client.post(
            add_url,
            {
                "user_identifier": "member1@example.com",
                "role": "Mentor",
                "is_active": True,
            },
        )
        membership = UserOrganizationMembership.objects.filter(
            organization=org, user=target_user
        ).first()
        self.assertIsNotNone(membership)
        self.assertEqual(membership.role, "Mentor")

        # 2. Update member role
        update_url = reverse(
            "platform_admin:organization_member_update",
            kwargs={"pk": org.pk, "member_id": membership.pk},
        )
        self.client.post(
            update_url,
            {
                "role": "LeadMentor",
                "is_active": "on",
            },
        )
        membership.refresh_from_db()
        self.assertEqual(membership.role, "LeadMentor")

        # 3. Delete member
        delete_url = reverse(
            "platform_admin:organization_member_delete",
            kwargs={"pk": org.pk, "member_id": membership.pk},
        )
        self.client.post(delete_url)
        self.assertFalse(
            UserOrganizationMembership.objects.filter(
                organization=org, user=target_user
            ).exists()
        )


class PlatformUserSupportAndImpersonationTests(TestCase):
    """Test user directory, search, support status toggling, and impersonation workflows."""

    def setUp(self):
        self.superuser = User.objects.create_superuser(
            username="admin", email="admin@example.com"
        )
        self.target_user = User.objects.create_user(
            username="janedoe", email="jane@example.com"
        )
        self.org = Organization.objects.create(name="Team Alpha", slug="alpha")
        self.membership = UserOrganizationMembership.objects.create(
            user=self.target_user, organization=self.org, role="Mentor", is_active=True
        )

    def test_user_search_and_filtering(self):
        """User directory allows searching by username, email, and filters."""
        self.client.force_login(self.superuser)
        url = reverse("platform_admin:user_support")

        # Search by username
        response = self.client.get(url, {"q": "janedoe"})
        self.assertContains(response, "jane@example.com")

        # Filter by role
        response_role = self.client.get(url, {"role": "Mentor"})
        self.assertContains(response_role, "jane@example.com")

    def test_user_toggle_status(self):
        """Support tool toggles regular user active status."""
        self.client.force_login(self.superuser)
        url = reverse(
            "platform_admin:user_toggle_status", kwargs={"pk": self.target_user.pk}
        )

        self.client.post(url)
        self.target_user.refresh_from_db()
        self.assertFalse(self.target_user.is_active)

        self.client.post(url)
        self.target_user.refresh_from_db()
        self.assertTrue(self.target_user.is_active)

    def test_impersonation_lifecycle(self):
        """Full impersonation lifecycle: start, session inspection, banner rendering, and exit."""
        self.client.force_login(self.superuser)

        # 1. Start impersonation
        start_url = reverse(
            "platform_admin:impersonate_user", kwargs={"pk": self.target_user.pk}
        )
        response = self.client.post(start_url)
        self.assertRedirects(response, reverse("profile_dashboard"))

        # Verify session state
        session = self.client.session
        self.assertEqual(session.get("impersonator_id"), self.superuser.pk)
        self.assertEqual(session.get("active_organization_id"), self.org.pk)

        # Verify logged in as target user
        home_resp = self.client.get(reverse("profile_dashboard"))
        self.assertEqual(home_resp.wsgi_request.user, self.target_user)
        self.assertTrue(home_resp.context.get("is_impersonating"))

        # Verify banner in rendered HTML
        self.assertContains(home_resp, "Impersonation Active")
        self.assertContains(home_resp, "Exit Impersonation")

        # Verify audit log recorded
        self.assertTrue(
            AuditLog.objects.filter(
                event=AuditEvent.USER_IMPERSONATED,
                actor=self.superuser,
            ).exists()
        )

        # 2. Stop impersonation
        stop_url = reverse("platform_admin:impersonate_stop")
        stop_resp = self.client.post(stop_url)
        self.assertRedirects(stop_resp, reverse("platform_admin:dashboard"))

        # Verify session is restored to superuser and impersonator_id removed
        dash_resp = self.client.get(reverse("platform_admin:dashboard"))
        self.assertEqual(dash_resp.wsgi_request.user, self.superuser)
        self.assertNotIn("impersonator_id", self.client.session)

        # Verify stop audit log recorded
        self.assertTrue(
            AuditLog.objects.filter(
                event=AuditEvent.IMPERSONATION_ENDED,
                actor=self.superuser,
            ).exists()
        )

    def test_cannot_impersonate_another_superuser(self):
        """Superusers cannot impersonate other superusers."""
        other_admin = User.objects.create_superuser(
            username="admin2", email="admin2@example.com"
        )
        self.client.force_login(self.superuser)

        url = reverse("platform_admin:impersonate_user", kwargs={"pk": other_admin.pk})
        response = self.client.post(url)
        self.assertRedirects(response, reverse("platform_admin:user_support"))

        # Session should still be original superuser
        self.assertNotIn("impersonator_id", self.client.session)

    def test_switch_organization_context(self):
        """Superuser can switch their active tenant organization context."""
        self.client.force_login(self.superuser)
        url = reverse(
            "platform_admin:organization_switch_context", kwargs={"pk": self.org.pk}
        )

        response = self.client.post(url)
        self.assertEqual(self.client.session.get("active_organization_id"), self.org.pk)


class PlatformAuditLogExplorerTests(TestCase):
    """Test viewing and filtering audit logs."""

    def setUp(self):
        self.superuser = User.objects.create_superuser(
            username="admin", email="admin@example.com"
        )
        self.client.force_login(self.superuser)

        AuditLog.objects.create(
            actor=self.superuser,
            event=AuditEvent.ORGANIZATION_CREATED,
            resource_type="Organization",
            resource_id="1",
            notes="Created test organization",
            outcome="success",
        )

    def test_audit_logs_explorer_view_and_filtering(self):
        """Audit logs view displays events and supports event filtering."""
        url = reverse("platform_admin:audit_logs")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Created test organization")

        # Filter by event
        filtered_resp = self.client.get(url, {"event": AuditEvent.ORGANIZATION_CREATED})
        self.assertEqual(len(filtered_resp.context["logs"]), 1)
