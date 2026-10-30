"""Tests for Lead Mentor role management and permissions.

Verifies:
1. UserOrganizationMembership with role="LeadMentor" grants full Lead Mentor access
   to non-superuser, non-staff users for their organization.
2. Regular mentors or non-lead users are denied Lead Mentor access.
3. Portal Settings Lead Mentors tab lists active lead mentors and eligible users.
4. Adding and removing Lead Mentors via PortalSettingsLeadMentorView works, creates/deletes
   memberships, prevents sole admin self-removal lockout, and logs audit events.
5. Confirmation safety requirements are satisfied.
"""

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from audit.events import AuditEvent
from audit.models import AuditLog
from organizations.models import Organization, UserOrganizationMembership
from programs.models import Adult
from programs.permission_views import (
    get_user_role,
    user_is_lead_mentor,
    user_is_mentor_or_lead,
)

User = get_user_model()


class LeadMentorRolePermissionTests(TestCase):
    def setUp(self):
        self.gos = Organization.objects.get(slug="gos")
        self.other_org = Organization.objects.create(
            name="Other Robotics", slug="other", subdomain="other"
        )

        # Standard non-superuser, non-staff user
        self.lead_user = User.objects.create_user(
            username="lead_coach",
            email="lead@example.com",
            first_name="Lead",
            last_name="Coach",
            is_staff=False,
            is_superuser=False,
        )
        self.lead_adult = Adult.objects.create(
            user=self.lead_user,
            legal_first_name="Lead",
            last_name="Coach",
            personal_email="lead@example.com",
            is_mentor=True,
            organization=self.gos,
        )
        # Grant org-level LeadMentor role
        self.membership = UserOrganizationMembership.objects.create(
            user=self.lead_user,
            organization=self.gos,
            role="LeadMentor",
            is_active=True,
        )

        # Regular mentor user (not lead mentor)
        self.mentor_user = User.objects.create_user(
            username="regular_mentor",
            email="mentor@example.com",
            first_name="Regular",
            last_name="Mentor",
            is_staff=False,
            is_superuser=False,
        )
        self.mentor_adult = Adult.objects.create(
            user=self.mentor_user,
            legal_first_name="Regular",
            last_name="Mentor",
            personal_email="mentor@example.com",
            is_mentor=True,
            organization=self.gos,
        )
        UserOrganizationMembership.objects.create(
            user=self.mentor_user,
            organization=self.gos,
            role="Mentor",
            is_active=True,
        )

        # Superuser (system admin)
        self.admin_user = User.objects.create_superuser(
            username="sysadmin",
            email="admin@example.com",
            password="adminpassword123",  # nosec B106
        )

        self.client = Client()

    def test_org_lead_mentor_detected_by_helpers(self):
        """A user with UserOrganizationMembership(role='LeadMentor') is recognized as Lead Mentor."""
        self.assertEqual(get_user_role(self.lead_user, org=self.gos), "LeadMentor")
        self.assertTrue(user_is_lead_mentor(self.lead_user, org=self.gos))
        self.assertTrue(user_is_mentor_or_lead(self.lead_user, org=self.gos))

        # But NOT a lead mentor in other_org
        self.assertEqual(get_user_role(self.lead_user, org=self.other_org), "Mentor")
        self.assertFalse(user_is_lead_mentor(self.lead_user, org=self.other_org))

    def test_regular_mentor_not_lead_mentor(self):
        """Regular mentor is not recognized as Lead Mentor."""
        self.assertEqual(get_user_role(self.mentor_user, org=self.gos), "Mentor")
        self.assertFalse(user_is_lead_mentor(self.mentor_user, org=self.gos))
        self.assertTrue(user_is_mentor_or_lead(self.mentor_user, org=self.gos))

    def test_superuser_is_lead_mentor_everywhere(self):
        """System admin (superuser) retains lead mentor status across all orgs."""
        self.assertEqual(get_user_role(self.admin_user, org=self.gos), "LeadMentor")
        self.assertTrue(user_is_lead_mentor(self.admin_user, org=self.gos))
        self.assertTrue(user_is_lead_mentor(self.admin_user, org=self.other_org))

    def test_org_lead_mentor_can_access_portal_settings(self):
        """An org Lead Mentor can access Portal Settings without being superuser or staff."""
        self.client.force_login(self.lead_user)
        response = self.client.get(reverse("portal_settings"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Lead Mentors")

    def test_regular_mentor_denied_portal_settings(self):
        """A regular mentor is denied access to Portal Settings."""
        self.client.force_login(self.mentor_user)
        response = self.client.get(reverse("portal_settings"))
        # Should redirect to home or return 403
        self.assertIn(response.status_code, (302, 403))


class PortalSettingsLeadMentorViewTests(TestCase):
    def setUp(self):
        self.gos = Organization.objects.get(slug="gos")

        self.admin_user = get_user_model().objects.create_superuser(
            username="sysadmin",
            email="admin@example.com",
            password="adminpassword123",  # nosec B106
        )

        self.lead_user = User.objects.create_user(
            username="lead_coach",
            email="lead@example.com",
            first_name="Lead",
            last_name="Coach",
            is_staff=False,
            is_superuser=False,
        )
        self.lead_membership = UserOrganizationMembership.objects.create(
            user=self.lead_user,
            organization=self.gos,
            role="LeadMentor",
            is_active=True,
        )

        self.candidate_user = User.objects.create_user(
            username="candidate_mentor",
            email="candidate@example.com",
            first_name="Candidate",
            last_name="Mentor",
            is_staff=False,
            is_superuser=False,
        )
        Adult.objects.create(
            user=self.candidate_user,
            legal_first_name="Candidate",
            last_name="Mentor",
            personal_email="candidate@example.com",
            is_mentor=True,
            organization=self.gos,
        )
        UserOrganizationMembership.objects.create(
            user=self.candidate_user,
            organization=self.gos,
            role="Mentor",
            is_active=True,
        )

        self.client = Client()

    def test_lead_mentors_tab_rendered(self):
        """Settings page displays current lead mentors and candidates on the lead_mentors tab with updated columns."""
        self.client.force_login(self.lead_user)
        response = self.client.get(reverse("portal_settings") + "?tab=lead_mentors")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "lead@example.com")
        self.assertContains(response, "Lead Coach")
        self.assertContains(response, "candidate@example.com")
        self.assertContains(response, "FULL access")
        self.assertContains(response, "Lead Mentor Security &amp; Authority")
        self.assertNotContains(response, "server-level superuser or staff")

        # Table columns verification: Name, Email, Assigned On, Last Logged In, Actions (no Username column)
        self.assertContains(response, "Assigned On")
        self.assertContains(response, "Last Logged In")
        self.assertNotContains(response, '<th style="width: 20%;">Username</th>')
        self.assertNotContains(response, "<th>Username</th>")

        # Verify no "Admin" badges are displayed even if a superuser is a member
        UserOrganizationMembership.objects.create(
            user=self.admin_user,
            organization=self.gos,
            role="LeadMentor",
            is_active=True,
        )
        response = self.client.get(reverse("portal_settings") + "?tab=lead_mentors")
        self.assertContains(response, "admin@example.com")
        self.assertNotContains(response, 'title="Superuser / System Admin"')
        self.assertNotContains(
            response,
            '<span class="badge bg-dark-subtle text-dark ms-1" title="Superuser / System Admin">Admin</span>',
        )
        self.assertNotContains(response, "Admin</span>")

        # Verify prominent placement: security callout box is positioned before the user table
        content = response.content.decode("utf-8")
        security_box_pos = content.find("Lead Mentor Security &amp; Authority")
        user_table_pos = content.find("lead@example.com")
        self.assertGreater(security_box_pos, -1)
        self.assertGreater(user_table_pos, -1)
        self.assertLess(security_box_pos, user_table_pos)

    def test_user_organization_membership_display_name_resolution(self):
        """Membership correctly resolves display name across Adult profiles, Student profiles, or User fallbacks."""
        # 1. Adult profile resolution
        membership = UserOrganizationMembership.objects.filter(
            user=self.lead_user,
            organization=self.gos,
            role="LeadMentor",
        ).first()
        self.assertEqual(membership.user_display_name, "Lead Coach")

        # 2. User with no profile falls back to full name or email/username
        user_no_profile = get_user_model().objects.create_user(
            username="standalone_user",
            email="standalone@example.com",
            first_name="Stand",
            last_name="Alone",
        )
        membership_no_profile = UserOrganizationMembership.objects.create(
            user=user_no_profile,
            organization=self.gos,
            role="LeadMentor",
        )
        self.assertEqual(membership_no_profile.user_display_name, "Stand Alone")

        # 3. User with only email / username
        user_bare = get_user_model().objects.create_user(
            username="bare_user",
            email="bare@example.com",
        )
        membership_bare = UserOrganizationMembership.objects.create(
            user=user_bare,
            organization=self.gos,
            role="LeadMentor",
        )
        self.assertEqual(membership_bare.user_display_name, "bare@example.com")

    def test_add_lead_mentor_action(self):
        """Lead mentor can promote an eligible user to Lead Mentor."""
        self.client.force_login(self.lead_user)
        url = reverse("portal_lead_mentors")
        response = self.client.post(
            url,
            {
                "action": "add_lead_mentor",
                "user_id": self.candidate_user.id,
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)

        # Check membership was created
        membership = UserOrganizationMembership.objects.filter(
            user=self.candidate_user,
            organization=self.gos,
            role="LeadMentor",
            is_active=True,
        ).first()
        self.assertIsNotNone(membership)

        # Verify audit log
        audit_entry = AuditLog.objects.filter(
            event=AuditEvent.ROLE_CHANGED,
            actor=self.lead_user,
        ).first()
        self.assertIsNotNone(audit_entry)

    def test_remove_lead_mentor_action(self):
        """Lead mentor can demote another Lead Mentor."""
        # Promote candidate first
        UserOrganizationMembership.objects.create(
            user=self.candidate_user,
            organization=self.gos,
            role="LeadMentor",
            is_active=True,
        )

        self.client.force_login(self.lead_user)
        url = reverse("portal_lead_mentors")
        response = self.client.post(
            url,
            {
                "action": "remove_lead_mentor",
                "user_id": self.candidate_user.id,
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)

        # Check membership was deactivated or deleted
        active_lead = UserOrganizationMembership.objects.filter(
            user=self.candidate_user,
            organization=self.gos,
            role="LeadMentor",
            is_active=True,
        ).exists()
        self.assertFalse(active_lead)

    def test_prevent_sole_lead_mentor_lockout(self):
        """A sole lead mentor cannot remove themselves and leave the organization with no admins."""
        self.client.force_login(self.lead_user)
        url = reverse("portal_lead_mentors")
        response = self.client.post(
            url,
            {
                "action": "remove_lead_mentor",
                "user_id": self.lead_user.id,
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cannot remove the only Lead Mentor")

        # Membership should still be active
        self.assertTrue(
            UserOrganizationMembership.objects.filter(
                user=self.lead_user,
                organization=self.gos,
                role="LeadMentor",
                is_active=True,
            ).exists()
        )

    def test_invalid_actions_and_missing_user(self):
        """View gracefully handles empty inputs and unknown actions."""
        self.client.force_login(self.lead_user)
        url = reverse("portal_lead_mentors")

        # Missing user_id for add
        r1 = self.client.post(
            url, {"action": "add_lead_mentor", "user_id": ""}, follow=True
        )
        self.assertEqual(r1.status_code, 200)
        self.assertContains(r1, "Please select a user")

        # Inactive / non-existent user
        r2 = self.client.post(
            url, {"action": "add_lead_mentor", "user_id": "999999"}, follow=True
        )
        self.assertEqual(r2.status_code, 200)
        self.assertContains(r2, "Selected user was not found")

        # Missing user_id for remove
        r3 = self.client.post(
            url, {"action": "remove_lead_mentor", "user_id": ""}, follow=True
        )
        self.assertEqual(r3.status_code, 200)
        self.assertContains(r3, "Target user not specified")

        # Unknown action
        r4 = self.client.post(url, {"action": "unknown_action"}, follow=True)
        self.assertEqual(r4.status_code, 200)
        self.assertContains(r4, "Unknown action")

    def test_org_lead_mentor_has_full_feature_access(self):
        """Org Lead Mentor can access admin/lead-mentor views without superuser or staff flag."""
        self.client.force_login(self.lead_user)

        views_to_test = [
            reverse("all_attendance"),
            reverse("student_merge"),
            reverse("parent_merge"),
            reverse("andrew_id_management"),
        ]
        for url in views_to_test:
            res = self.client.get(url)
            self.assertEqual(
                res.status_code, 200, f"Expected 200 for {url}, got {res.status_code}"
            )
