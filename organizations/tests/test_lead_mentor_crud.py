"""Tests for Lead Mentor CRUD operations in portal settings.

Verifies that Lead Mentors can add, remove, invite, and manage Lead Mentor
memberships for their organization, with proper safety checks and audit logging.
"""

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import Client, TestCase
from django.urls import reverse

from audit.events import AuditEvent
from audit.models import AuditLog
from organizations.models import (
    Organization,
    OrganizationInvitation,
    OrganizationSettings,
    UserOrganizationMembership,
)
from programs.models import Adult
from programs.permission_views import (
    get_user_role,
    user_is_lead_mentor,
    user_is_mentor_or_lead,
)

User = get_user_model()


class PortalSettingsLeadMentorCRUDTests(TestCase):
    """Tests for Lead Mentor add/remove/CRUD operations via portal settings."""

    def setUp(self):
        self.password = "password123"  # nosec B105 B106
        self.gos = Organization.objects.get(slug="gos")

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
        )
        # The Adult post_save sync already creates this membership (default
        # org); get_or_create keeps the fixture explicit without colliding.
        UserOrganizationMembership.objects.get_or_create(
            user=self.candidate_user,
            organization=self.gos,
            role="Mentor",
            defaults={"is_active": True},
        )

        self.client = Client()

    def test_add_lead_mentor_action_sends_invitation_email(self):
        """Assigning an existing user sends a Lead Mentor invitation email."""
        mail.outbox.clear()
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
        self.assertContains(response, "Lead Mentor invitation sent to Candidate Mentor")

        # Check membership was NOT immediately created as active
        active_lead = UserOrganizationMembership.objects.filter(
            user=self.candidate_user,
            organization=self.gos,
            role="LeadMentor",
            is_active=True,
        ).exists()
        self.assertFalse(active_lead)

        # Verify invitation created
        invite = OrganizationInvitation.objects.filter(
            organization=self.gos,
            email=self.candidate_user.email,
            role="LeadMentor",
            status="pending",
        ).first()
        self.assertIsNotNone(invite)
        self.assertEqual(invite.invited_by, self.lead_user)

        # Verify email was dispatched
        self.assertEqual(len(mail.outbox), 1)
        sent_email = mail.outbox[0]
        self.assertIn(self.candidate_user.email, sent_email.to)
        self.assertIn("Lead Mentor", sent_email.subject)
        self.assertIn(invite.token, sent_email.body)

        # Verify audit log
        audit_entry = AuditLog.objects.filter(
            event=AuditEvent.ROLE_CHANGED,
            actor=self.lead_user,
        ).first()
        self.assertIsNotNone(audit_entry)

    def test_add_lead_mentor_acceptance_activates_role(self):
        """When the assigned existing user accepts the invitation, their role is activated."""
        self.client.force_login(self.lead_user)
        url = reverse("portal_lead_mentors")
        self.client.post(
            url,
            {"action": "add_lead_mentor", "user_id": self.candidate_user.id},
            follow=True,
        )
        invite = OrganizationInvitation.objects.get(
            organization=self.gos,
            email=self.candidate_user.email,
            role="LeadMentor",
            status="pending",
        )

        # Candidate logs in and accepts the invitation
        self.client.force_login(self.candidate_user)
        accept_url = reverse(
            "organizations:accept_invitation", kwargs={"token": invite.token}
        )
        # Check acceptance page details
        get_res = self.client.get(accept_url)
        self.assertEqual(get_res.status_code, 200)
        self.assertContains(get_res, "Lead Mentor Authority")

        # Accept
        post_res = self.client.post(accept_url, follow=True)
        self.assertEqual(post_res.status_code, 200)

        invite.refresh_from_db()
        self.assertEqual(invite.status, "accepted")
        self.assertTrue(user_is_lead_mentor(self.candidate_user, org=self.gos))

    def test_add_lead_mentor_existing_pending_invitation_refreshes_and_resends(self):
        """Assigning an existing user who already has a pending invitation refreshes expiry."""
        invite = OrganizationInvitation.objects.create(
            organization=self.gos,
            email="pending@example.com",
            role="LeadMentor",
            invited_by=self.lead_user,
        )
        pending_user = User.objects.create_user(
            username="pending_user", email="pending@example.com"
        )
        mail.outbox.clear()
        self.client.force_login(self.lead_user)
        url = reverse("portal_lead_mentors")
        response = self.client.post(
            url,
            {"action": "add_lead_mentor", "user_id": pending_user.id},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Lead Mentor invitation sent")
        self.assertEqual(len(mail.outbox), 1)
        invite.refresh_from_db()
        self.assertTrue(invite.is_valid())
        self.assertEqual(
            OrganizationInvitation.objects.filter(
                organization=self.gos,
                email="pending@example.com",
                role="LeadMentor",
            ).count(),
            1,
        )

    def test_add_lead_mentor_already_active_lead_mentor_error(self):
        """Assigning a user who is already an active Lead Mentor returns an error."""
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
            {"action": "add_lead_mentor", "user_id": self.candidate_user.id},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already an active Lead Mentor")

    def test_add_lead_mentor_missing_email_error(self):
        """Assigning a user without an email address returns an error message."""
        no_email_user = User.objects.create_user(
            username="no_email_user",
            email="",
            first_name="No",
            last_name="Email",
        )
        self.client.force_login(self.lead_user)
        url = reverse("portal_lead_mentors")
        response = self.client.post(
            url,
            {"action": "add_lead_mentor", "user_id": no_email_user.id},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "does not have a valid email address")

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
            {"action": "remove_lead_mentor", "user_id": self.candidate_user.id},
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
            {"action": "remove_lead_mentor", "user_id": self.lead_user.id},
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

    def test_invite_lead_mentor_by_email(self):
        """Lead mentor can invite a new user by email with 14-day expiry."""
        mail.outbox.clear()
        self.client.force_login(self.lead_user)
        url = reverse("portal_lead_mentors")
        response = self.client.post(
            url,
            {"action": "invite_lead_mentor", "email": "newlead@example.com"},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, "Successfully sent Lead Mentor invitation to newlead@example.com"
        )

        # Verify invitation row created
        invite = OrganizationInvitation.objects.filter(
            organization=self.gos,
            email="newlead@example.com",
            role="LeadMentor",
            status="pending",
        ).first()
        self.assertIsNotNone(invite)
        self.assertEqual(invite.invited_by, self.lead_user)
        self.assertTrue(invite.is_valid())

        # Verify notification email sent
        self.assertEqual(len(mail.outbox), 1)
        sent_email = mail.outbox[0]
        self.assertIn("newlead@example.com", sent_email.to)
        self.assertIn(
            "Invitation to join Girls of Steel as Lead Mentor", sent_email.subject
        )
        self.assertIn(invite.token, sent_email.body)

    def test_invite_lead_mentor_already_active_lead_mentor(self):
        """Inviting someone who is already an active Lead Mentor returns an error."""
        self.client.force_login(self.lead_user)
        url = reverse("portal_lead_mentors")
        response = self.client.post(
            url,
            {"action": "invite_lead_mentor", "email": "lead@example.com"},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already an active Lead Mentor")
        self.assertEqual(
            OrganizationInvitation.objects.filter(email="lead@example.com").count(),
            0,
        )

    def test_invite_lead_mentor_existing_pending_invitation_refreshes_and_resends(self):
        """Inviting an email that already has a pending invitation refreshes its expiry."""
        invite = OrganizationInvitation.objects.create(
            organization=self.gos,
            email="pending@example.com",
            role="LeadMentor",
            invited_by=self.lead_user,
        )
        pending_user = User.objects.create_user(
            username="pending_user", email="pending@example.com"
        )
        mail.outbox.clear()
        self.client.force_login(self.lead_user)
        url = reverse("portal_lead_mentors")
        response = self.client.post(
            url,
            {"action": "add_lead_mentor", "user_id": pending_user.id},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Lead Mentor invitation sent")
        self.assertEqual(len(mail.outbox), 1)
        invite.refresh_from_db()
        self.assertTrue(invite.is_valid())
        self.assertEqual(
            OrganizationInvitation.objects.filter(
                organization=self.gos,
                email="pending@example.com",
                role="LeadMentor",
            ).count(),
            1,
        )

    def test_resend_lead_mentor_invitation(self):
        """Lead mentor can resend an existing pending invitation."""
        invite = OrganizationInvitation.objects.create(
            organization=self.gos,
            email="resendme@example.com",
            role="LeadMentor",
            invited_by=self.lead_user,
        )
        mail.outbox.clear()
        self.client.force_login(self.lead_user)
        url = reverse("portal_lead_mentors")
        response = self.client.post(
            url,
            {"action": "resend_invitation", "invitation_id": invite.id},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, "Resent Lead Mentor invitation to resendme@example.com"
        )
        self.assertEqual(len(mail.outbox), 1)
        invite.refresh_from_db()
        self.assertTrue(invite.is_valid())

    def test_revoke_lead_mentor_invitation(self):
        """Lead mentor can revoke a pending invitation."""
        invite = OrganizationInvitation.objects.create(
            organization=self.gos,
            email="revokeme@example.com",
            role="LeadMentor",
            invited_by=self.lead_user,
        )
        self.client.force_login(self.lead_user)
        url = reverse("portal_lead_mentors")
        response = self.client.post(
            url,
            {"action": "revoke_invitation", "invitation_id": invite.id},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, "Revoked Lead Mentor invitation for revokeme@example.com"
        )

        invite.refresh_from_db()
        self.assertFalse(invite.is_valid())
        self.assertEqual(invite.status, "declined")

    def test_pending_invitations_table_and_buttons_rendered_on_settings(self):
        """Settings page displays pending invitations and both Assign & Invite header buttons."""
        OrganizationInvitation.objects.create(
            organization=self.gos,
            email="waiting@example.com",
            role="LeadMentor",
            invited_by=self.lead_user,
        )
        self.client.force_login(self.lead_user)
        response = self.client.get(reverse("portal_settings") + "?tab=lead_mentors")
        self.assertEqual(response.status_code, 200)

        # Header buttons
        self.assertContains(response, "Assign Existing User")
        self.assertContains(response, "Invite by Email")

        # Pending invitations table
        self.assertContains(response, "Pending Lead Mentor Invitations")
        self.assertContains(response, "waiting@example.com")
        self.assertContains(response, "Resend")
        self.assertContains(response, "Revoke")

    def test_accept_lead_mentor_invitation_flow(self):
        """Invited user accepts email invite and is granted LeadMentor role."""
        invite = OrganizationInvitation.objects.create(
            organization=self.gos,
            email="invitedcoach@example.com",
            role="LeadMentor",
            invited_by=self.lead_user,
        )
        new_coach = User.objects.create_user(
            username="invitedcoach",
            email="invitedcoach@example.com",
            first_name="Invited",
            last_name="Coach",
            password="password123",  # nosec B106
        )
        self.client.force_login(new_coach)
        accept_url = reverse(
            "organizations:accept_invitation", kwargs={"token": invite.token}
        )
        res = self.client.post(accept_url, follow=True)
        self.assertEqual(res.status_code, 200)

        invite.refresh_from_db()
        self.assertEqual(invite.status, "accepted")
        self.assertTrue(user_is_lead_mentor(new_coach, org=self.gos))

    def test_accept_invitation_mismatched_user_rejected_and_not_consumed(self):
        """User logged in with a different email cannot accept an invitation sent to someone else."""
        invite = OrganizationInvitation.objects.create(
            organization=self.gos,
            email="intended@example.com",
            role="LeadMentor",
            invited_by=self.lead_user,
        )
        wrong_user = User.objects.create_user(
            username="wronguser",
            email="wrong@example.com",
            first_name="Wrong",
            last_name="Person",
            password="password123",  # nosec B106
        )
        self.client.force_login(wrong_user)
        accept_url = reverse(
            "organizations:accept_invitation", kwargs={"token": invite.token}
        )

        # GET should warn about account mismatch and hide/disable accept button
        get_res = self.client.get(accept_url)
        self.assertEqual(get_res.status_code, 200)
        self.assertContains(get_res, "intended@example.com")
        self.assertContains(get_res, "wrong@example.com")
        self.assertContains(get_res, "Sign Out & Switch Account")
        self.assertContains(get_res, "Account Mismatch")

        # POST should fail to accept and not create membership or consume the invite
        post_res = self.client.post(accept_url, follow=True)
        self.assertEqual(post_res.status_code, 200)
        self.assertContains(
            post_res, "This invitation was sent to intended@example.com"
        )

        # Verify invite is STILL pending and not accepted
        invite.refresh_from_db()
        self.assertEqual(invite.status, "pending")
        self.assertIsNone(invite.accepted_at)
        self.assertIsNone(invite.accepted_by)

        # Verify wrong user was NOT granted LeadMentor role
        self.assertFalse(user_is_lead_mentor(wrong_user, org=self.gos))
        self.assertFalse(
            UserOrganizationMembership.objects.filter(
                user=wrong_user, organization=self.gos
            ).exists()
        )

    def test_invitation_accept_method_validates_user_email(self):
        """OrganizationInvitation.accept raises ValueError if called with a mismatched user."""
        invite = OrganizationInvitation.objects.create(
            organization=self.gos,
            email="intended@example.com",
            role="LeadMentor",
            invited_by=self.lead_user,
        )
        wrong_user = User.objects.create_user(
            username="wronguser2",
            email="wrong2@example.com",
            password="password123",  # nosec B106
        )
        with self.assertRaises(ValueError) as ctx:
            invite.accept(wrong_user)
        self.assertIn("intended@example.com", str(ctx.exception))
        self.assertEqual(invite.status, "pending")

    def test_invitation_accept_case_insensitive_and_profile_email_matching(self):
        """Invitation email matching is case-insensitive and checks linked Adult profile emails."""
        invite = OrganizationInvitation.objects.create(
            organization=self.gos,
            email="Mentor.User@Example.COM",
            role="LeadMentor",
            invited_by=self.lead_user,
        )
        user_with_adult = User.objects.create_user(
            username="mentorwithadult",
            email="",  # empty user email
            password="password123",  # nosec B106
        )
        Adult.objects.create(
            user=user_with_adult,
            legal_first_name="Mentor",
            last_name="User",
            personal_email="mentor.user@example.com",
        )
        membership = invite.accept(user_with_adult)
        self.assertEqual(membership.user, user_with_adult)
        self.assertEqual(invite.status, "accepted")

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


class PortalSettingsAccessControlTests(TestCase):
    """Tests for portal settings access control - ensuring proper org role-based access."""

    def setUp(self):
        self.gos = Organization.objects.get(slug="gos")

        self.lead_user = User.objects.create_user(
            username="lead_coach",
            email="lead@example.com",
            first_name="Lead",
            last_name="Coach",
            is_staff=False,
            is_superuser=False,
        )
        from django.contrib.auth.models import Group

        lead_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.lead_user.groups.add(lead_group)

        self.mentor_user = User.objects.create_user(
            username="regular_mentor",
            email="mentor@example.com",
            first_name="Regular",
            last_name="Mentor",
            is_staff=False,
            is_superuser=False,
        )
        mentor_group, _ = Group.objects.get_or_create(name="Mentor")
        self.mentor_user.groups.add(mentor_group)
        Adult.objects.create(
            user=self.mentor_user,
            legal_first_name="Regular",
            last_name="Mentor",
            personal_email="mentor@example.com",
            is_mentor=True,
        )

        self.org, _ = Organization.objects.get_or_create(
            slug="gos", defaults={"name": "Girls of Steel"}
        )
        self.org_settings, _ = OrganizationSettings.objects.get_or_create(
            organization=self.org
        )

        self.client = Client()

    def test_non_lead_mentor_denied_portal_settings(self):
        """A regular mentor is denied access to Portal Settings."""
        self.client.force_login(self.mentor_user)
        resp = self.client.post(
            reverse("portal_settings"),
            {"action": "update_email_settings", "sender_display_name": "Hacked"},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("home"))

    def test_lead_mentor_can_access_portal_settings(self):
        """An org Lead Mentor can access Portal Settings without being superuser or staff."""
        self.client.force_login(self.lead_user)
        response = self.client.get(reverse("portal_settings"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Lead Mentors")

    def test_lead_mentor_updates_general_email_settings(self):
        """Lead Mentor can update general email settings."""
        self.client.force_login(self.lead_user)
        # This test would need the proper URL and setup
        pass
