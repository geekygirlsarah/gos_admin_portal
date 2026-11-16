"""Tests for the scoped legacy fallback + membership sync (Option 1).

Two behaviors are covered here:

1. **Scoped fallback**: users with *no* ``UserOrganizationMembership`` rows
   anywhere keep the legacy group/profile-based access (org-scoped calls
   included), while users that do have membership rows are strictly scoped to
   the organization being checked (cross-org access is denied).
2. **Membership sync**: saving an Adult/Student profile creates the matching
   memberships (mirroring migration 0003), and an idempotent management
   command backfills memberships for profiles created before the sync existed.
"""

from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import TestCase

from organizations.models import Organization, UserOrganizationMembership
from programs.models import Adult, Student
from programs.permission_views import (
    can_user_delete,
    get_user_role,
    user_is_alumni,
    user_is_lead_mentor,
    user_is_mentor,
    user_is_mentor_or_lead,
    user_is_parent,
)

User = get_user_model()


class ScopedFallbackTests(TestCase):
    """No membership rows anywhere -> legacy access; any rows -> strict org scope."""

    def setUp(self):
        self.gos = Organization.objects.get(slug="gos")
        self.other_org = Organization.objects.create(
            name="Other Robotics", slug="other-org", subdomain="other-org"
        )

    def _strip_memberships(self, user):
        """Simulate a user created before memberships existed (the prod gap)."""
        UserOrganizationMembership.objects.filter(user=user).delete()

    def test_group_only_lead_mentor_works_in_any_org(self):
        """Legacy LeadMentor group users keep lead access org-scoped."""
        user = User.objects.create_user(
            username="legacy_lead", password="password123"  # nosec B106
        )
        lm_group, _ = Group.objects.get_or_create(name="LeadMentor")
        user.groups.add(lm_group)

        self.assertTrue(user_is_lead_mentor(user, org=self.gos))
        self.assertTrue(user_is_lead_mentor(user, org=self.other_org))
        self.assertTrue(user_is_mentor_or_lead(user, org=self.gos))
        self.assertEqual(get_user_role(user, org=self.gos), "LeadMentor")
        self.assertEqual(get_user_role(user, org=self.other_org), "LeadMentor")

    def test_profile_user_without_memberships_falls_back_to_flags(self):
        """A profile user with no membership rows keeps legacy flag-based roles
        even when an explicit org is passed (the pre-backfill prod state)."""
        user = User.objects.create_user(
            username="legacy_mentor", password="password123"  # nosec B106
        )
        Adult.objects.create(
            user=user,
            legal_first_name="Legacy",
            last_name="Mentor",
            is_mentor=True,
            mentor_active=True,
            organization=self.gos,
        )
        self._strip_memberships(user)

        self.assertEqual(get_user_role(user, org=self.gos), "Mentor")
        self.assertEqual(get_user_role(user, org=self.other_org), "Mentor")
        self.assertTrue(user_is_mentor(user, org=self.gos))
        self.assertTrue(user_is_mentor(user, org=self.other_org))
        self.assertFalse(user_is_lead_mentor(user, org=self.gos))

    def test_profile_parent_and_student_without_memberships_fall_back(self):
        parent_user = User.objects.create_user(
            username="legacy_parent", password="password123"  # nosec B106
        )
        Adult.objects.create(
            user=parent_user,
            legal_first_name="Legacy",
            last_name="Parent",
            is_parent=True,
            organization=self.gos,
        )
        self._strip_memberships(parent_user)

        student_user = User.objects.create_user(
            username="legacy_student", password="password123"  # nosec B106
        )
        Student.objects.create(
            user=student_user,
            legal_first_name="Legacy",
            last_name="Student",
            organization=self.gos,
        )
        self._strip_memberships(student_user)

        self.assertEqual(get_user_role(parent_user, org=self.other_org), "Parent")
        self.assertTrue(user_is_parent(parent_user, org=self.other_org))
        self.assertEqual(get_user_role(student_user, org=self.other_org), "Student")

    def test_membership_user_denied_in_other_org(self):
        """Any membership row makes the user strictly org-scoped: no falling
        back to global profile/group flags for another organization."""
        user = User.objects.create_user(
            username="scoped_mentor", password="password123"  # nosec B106
        )
        Adult.objects.create(
            user=user,
            legal_first_name="Scoped",
            last_name="Mentor",
            is_mentor=True,
            mentor_active=True,
            organization=self.gos,
        )
        # The sync signal creates the gos membership; verify, then deny elsewhere.
        self.assertTrue(
            UserOrganizationMembership.objects.filter(
                user=user, organization=self.gos, role="Mentor"
            ).exists()
        )

        self.assertEqual(get_user_role(user, org=self.other_org), None)
        self.assertFalse(user_is_mentor(user, org=self.other_org))
        self.assertFalse(user_is_lead_mentor(user, org=self.other_org))
        self.assertFalse(user_is_mentor_or_lead(user, org=self.other_org))
        self.assertFalse(user_is_parent(user, org=self.other_org))

    def test_membership_user_resolved_in_own_org(self):
        user = User.objects.create_user(
            username="scoped_parent", password="password123"  # nosec B106
        )
        Adult.objects.create(
            user=user,
            legal_first_name="Scoped",
            last_name="Parent",
            is_parent=True,
            organization=self.gos,
        )

        self.assertEqual(get_user_role(user, org=self.gos), "Parent")
        self.assertTrue(user_is_parent(user, org=self.gos))
        self.assertFalse(user_is_lead_mentor(user, org=self.gos))

    def test_inactive_memberships_are_denied(self):
        user = User.objects.create_user(
            username="suspended_mentor", password="password123"  # nosec B106
        )
        Adult.objects.create(
            user=user,
            legal_first_name="Suspended",
            last_name="Mentor",
            is_mentor=True,
            mentor_active=True,
            organization=self.gos,
        )
        UserOrganizationMembership.objects.filter(user=user).update(is_active=False)

        self.assertEqual(get_user_role(user, org=self.gos), None)
        self.assertFalse(user_is_mentor(user, org=self.gos))

    def test_missing_org_denies_membership_role_but_explicit_org_resolves(self):
        """Membership roles are not inferred without an organization context."""
        user = User.objects.create_user(
            username="member_only", password="password123"  # nosec B106
        )
        UserOrganizationMembership.objects.create(
            user=user,
            organization=self.gos,
            role="LeadMentor",
            is_active=True,
        )

        self.assertIsNone(get_user_role(user))
        self.assertFalse(user_is_lead_mentor(user))
        self.assertEqual(get_user_role(user, org=self.gos), "LeadMentor")
        self.assertTrue(user_is_lead_mentor(user, org=self.gos))

    def test_inactive_membership_org_none_denied(self):
        user = User.objects.create_user(
            username="inactive_member", password="password123"  # nosec B106
        )
        UserOrganizationMembership.objects.create(
            user=user,
            organization=self.gos,
            role="Mentor",
            is_active=False,
        )

        self.assertIsNone(get_user_role(user))
        self.assertFalse(user_is_mentor(user))

    def test_lead_mentor_group_wins_over_profile_memberships(self):
        """Active lead memberships retain lead access alongside profile roles."""
        user = User.objects.create_user(
            username="group_lead_with_profile", password="password123"  # nosec B106
        )
        Adult.objects.create(
            user=user,
            legal_first_name="Group",
            last_name="Lead",
            is_mentor=True,
            organization=self.gos,
        )
        lm_group, _ = Group.objects.get_or_create(name="LeadMentor")
        user.groups.add(lm_group)
        for org in (self.gos, self.other_org):
            UserOrganizationMembership.objects.create(
                user=user, organization=org, role="LeadMentor", is_active=True
            )

        self.assertEqual(get_user_role(user, org=self.gos), "LeadMentor")
        self.assertTrue(user_is_lead_mentor(user, org=self.gos))
        self.assertTrue(user_is_lead_mentor(user, org=self.other_org))

    def test_cross_org_member_gets_none_in_target_org(self):
        user = User.objects.create_user(
            username="other_org_member", password="password123"  # nosec B106
        )
        UserOrganizationMembership.objects.create(
            user=user,
            organization=self.other_org,
            role="LeadMentor",
            is_active=True,
        )

        self.assertIsNone(get_user_role(user, org=self.gos))
        self.assertFalse(user_is_lead_mentor(user, org=self.gos))
        self.assertEqual(get_user_role(user, org=self.other_org), "LeadMentor")

    def test_group_lead_with_lesser_memberships_keeps_only_active_roles(self):
        user = User.objects.create_user(username="group_lead_lesser_roles")
        user.groups.add(Group.objects.get_or_create(name="LeadMentor")[0])
        for role in ("Mentor", "Parent", "Alumni"):
            UserOrganizationMembership.objects.create(
                user=user, organization=self.gos, role=role, is_active=True
            )

        self.assertEqual(get_user_role(user, org=self.gos), "Mentor")
        self.assertFalse(user_is_lead_mentor(user, org=self.gos))
        self.assertTrue(user_is_mentor(user, org=self.gos))
        self.assertTrue(user_is_parent(user, org=self.gos))
        self.assertTrue(user_is_alumni(user, org=self.gos))
        self.assertTrue(user_is_mentor_or_lead(user, org=self.gos))
        self.assertFalse(can_user_delete(user, "attendance", org=self.gos))

    def test_group_lead_boolean_helpers_require_active_scoped_membership(self):
        user = User.objects.create_user(username="group_lead_boolean_helpers")
        user.groups.add(Group.objects.get_or_create(name="LeadMentor")[0])
        UserOrganizationMembership.objects.create(
            user=user, organization=self.gos, role="Parent", is_active=True
        )
        UserOrganizationMembership.objects.create(
            user=user, organization=self.gos, role="LeadMentor", is_active=False
        )

        for org in (self.gos, self.other_org):
            for helper in (user_is_lead_mentor, user_is_mentor_or_lead):
                with self.subTest(org=org, helper=helper.__name__):
                    self.assertFalse(helper(user, org=org))

    def test_group_lead_membership_does_not_grant_cross_org_access(self):
        user = User.objects.create_user(username="group_lead_cross_org")
        user.groups.add(Group.objects.get_or_create(name="LeadMentor")[0])
        UserOrganizationMembership.objects.create(
            user=user, organization=self.gos, role="LeadMentor", is_active=True
        )

        self.assertEqual(get_user_role(user, org=self.gos), "LeadMentor")
        self.assertTrue(user_is_lead_mentor(user, org=self.gos))
        self.assertTrue(can_user_delete(user, "programs", org=self.gos))
        self.assertIsNone(get_user_role(user, org=self.other_org))
        self.assertFalse(user_is_lead_mentor(user, org=self.other_org))
        self.assertFalse(user_is_mentor_or_lead(user, org=self.other_org))
        self.assertFalse(can_user_delete(user, "programs", org=self.other_org))

    def test_group_lead_with_inactive_lead_membership_is_not_lead(self):
        user = User.objects.create_user(username="group_lead_inactive")
        user.groups.add(Group.objects.get_or_create(name="LeadMentor")[0])
        UserOrganizationMembership.objects.create(
            user=user, organization=self.gos, role="LeadMentor", is_active=False
        )

        self.assertIsNone(get_user_role(user, org=self.gos))
        self.assertFalse(user_is_lead_mentor(user, org=self.gos))
        self.assertFalse(user_is_mentor_or_lead(user, org=self.gos))
        self.assertFalse(can_user_delete(user, "programs", org=self.gos))

        UserOrganizationMembership.objects.create(
            user=user, organization=self.gos, role="Parent", is_active=True
        )
        self.assertEqual(get_user_role(user, org=self.gos), "Parent")
        self.assertTrue(user_is_parent(user, org=self.gos))
        self.assertFalse(user_is_lead_mentor(user, org=self.gos))

    def test_group_lead_without_memberships_takes_precedence_over_profile(self):
        user = User.objects.create_user(username="legacy_lead_with_profile")
        Adult.objects.create(
            user=user,
            legal_first_name="Legacy",
            last_name="Lead",
            is_mentor=True,
            organization=self.gos,
        )
        self._strip_memberships(user)
        user.groups.add(Group.objects.get_or_create(name="LeadMentor")[0])

        self.assertEqual(get_user_role(user, org=self.other_org), "LeadMentor")
        self.assertTrue(user_is_lead_mentor(user, org=self.other_org))

    def test_superuser_retains_access_with_inactive_membership(self):
        user = User.objects.create_superuser(username="superuser", password=None)
        UserOrganizationMembership.objects.create(
            user=user, organization=self.gos, role="LeadMentor", is_active=False
        )

        for org in (self.gos, self.other_org):
            with self.subTest(org=org):
                self.assertEqual(get_user_role(user, org=org), "LeadMentor")
                self.assertTrue(user_is_lead_mentor(user, org=org))
                self.assertTrue(user_is_mentor_or_lead(user, org=org))
                self.assertTrue(can_user_delete(user, "programs", org=org))

    def test_missing_default_org_denies_membership_users_legacy_flags(self):
        user = User.objects.create_user(username="missing_default_org")
        Adult.objects.create(
            user=user,
            legal_first_name="Missing",
            last_name="Default",
            is_mentor=True,
            is_parent=True,
            is_alumni=True,
            organization=self.gos,
        )
        user.groups.add(Group.objects.get_or_create(name="LeadMentor")[0])

        for outcome in (None, RuntimeError("no default organization")):
            with self.subTest(outcome=outcome):
                with patch(
                    "organizations.utils.get_default_organization",
                    side_effect=outcome if isinstance(outcome, Exception) else None,
                    return_value=None,
                ):
                    self.assertIsNone(get_user_role(user))
                    for helper in (
                        user_is_lead_mentor,
                        user_is_mentor,
                        user_is_parent,
                        user_is_alumni,
                        user_is_mentor_or_lead,
                    ):
                        with self.subTest(helper=helper.__name__):
                            self.assertFalse(helper(user))

    def test_legacy_group_fallback_requires_explicit_org(self):
        user = User.objects.create_user(username="missing_default_legacy")
        user.groups.add(Group.objects.get_or_create(name="LeadMentor")[0])

        with patch("organizations.utils.get_default_organization", return_value=None):
            self.assertIsNone(get_user_role(user))
            self.assertFalse(user_is_lead_mentor(user))
            self.assertEqual(get_user_role(user, org=self.gos), "LeadMentor")
            self.assertTrue(user_is_lead_mentor(user, org=self.gos))


class ProfileMembershipSyncTests(TestCase):
    """post_save receivers keep UserOrganizationMembership in sync with profiles."""

    def setUp(self):
        self.gos = Organization.objects.get(slug="gos")
        self.other_org = Organization.objects.create(
            name="Other Robotics", slug="other-org", subdomain="other-org"
        )

    def _memberships(self, user):
        return set(
            UserOrganizationMembership.objects.filter(user=user).values_list(
                "organization_id", "role", "is_active"
            )
        )

    def test_adult_mentor_gets_membership(self):
        user = User.objects.create_user(
            username="mentor1", password="password123"  # nosec B106
        )
        Adult.objects.create(
            user=user,
            legal_first_name="New",
            last_name="Mentor",
            is_mentor=True,
            organization=self.gos,
        )
        self.assertIn((self.gos.pk, "Mentor", True), self._memberships(user))

    def test_inactive_mentor_gets_inactive_membership(self):
        user = User.objects.create_user(
            username="mentor2", password="password123"  # nosec B106
        )
        Adult.objects.create(
            user=user,
            legal_first_name="New",
            last_name="Mentor",
            is_mentor=True,
            mentor_active=False,
            organization=self.gos,
        )
        self.assertIn((self.gos.pk, "Mentor", False), self._memberships(user))

    def test_multi_role_adult_gets_all_memberships(self):
        user = User.objects.create_user(
            username="multi", password="password123"  # nosec B106
        )
        Adult.objects.create(
            user=user,
            legal_first_name="Multi",
            last_name="Role",
            is_mentor=True,
            is_parent=True,
            is_alumni=True,
            organization=self.gos,
        )
        self.assertEqual(
            self._memberships(user),
            {
                (self.gos.pk, "Mentor", True),
                (self.gos.pk, "Parent", True),
                (self.gos.pk, "Alumni", True),
            },
        )

    def test_adult_without_org_uses_default_organization(self):
        user = User.objects.create_user(
            username="noorg_adult", password="password123"  # nosec B106
        )
        Adult.objects.create(
            user=user,
            legal_first_name="No",
            last_name="Org",
            is_parent=True,
        )
        self.assertIn((self.gos.pk, "Parent", True), self._memberships(user))

    def test_adult_membership_scoped_to_profile_org(self):
        user = User.objects.create_user(
            username="other_adult", password="password123"  # nosec B106
        )
        Adult.objects.create(
            user=user,
            legal_first_name="Other",
            last_name="Org",
            is_mentor=True,
            organization=self.other_org,
        )
        self.assertEqual(self._memberships(user), {(self.other_org.pk, "Mentor", True)})

    def test_adult_without_user_creates_nothing(self):
        Adult.objects.create(
            legal_first_name="No",
            last_name="User",
            is_mentor=True,
            organization=self.gos,
        )
        self.assertEqual(UserOrganizationMembership.objects.count(), 0)

    def test_repeated_saves_do_not_duplicate(self):
        user = User.objects.create_user(
            username="repeat", password="password123"  # nosec B106
        )
        adult = Adult.objects.create(
            user=user,
            legal_first_name="Repeat",
            last_name="Save",
            is_mentor=True,
            is_parent=True,
            organization=self.gos,
        )
        adult.save()
        adult.save()
        self.assertEqual(
            UserOrganizationMembership.objects.filter(user=user).count(), 2
        )

    def test_mentor_deactivation_propagates_to_membership(self):
        """Flipping ``mentor_active`` off deactivates the Mentor membership so
        the legacy flag transition keeps working org-scoped."""
        user = User.objects.create_user(
            username="deactivating", password="password123"  # nosec B106
        )
        adult = Adult.objects.create(
            user=user,
            legal_first_name="Deact",
            last_name="Mentor",
            is_mentor=True,
            mentor_active=True,
            organization=self.gos,
        )
        self.assertTrue(
            UserOrganizationMembership.objects.get(
                user=user, organization=self.gos, role="Mentor"
            ).is_active
        )

        adult.mentor_active = False
        adult.save()

        self.assertFalse(
            UserOrganizationMembership.objects.get(
                user=user, organization=self.gos, role="Mentor"
            ).is_active
        )
        self.assertIsNone(get_user_role(user, org=self.gos))
        self.assertFalse(user_is_mentor(user, org=self.gos))

    def test_profile_save_never_reactivates_membership(self):
        """A profile save must not silently lift a deactivated membership
        (platform-admin deactivation stays until explicitly reversed)."""
        user = User.objects.create_user(
            username="reactivating", password="password123"  # nosec B106
        )
        adult = Adult.objects.create(
            user=user,
            legal_first_name="React",
            last_name="Mentor",
            is_mentor=True,
            mentor_active=False,
            organization=self.gos,
        )
        self.assertFalse(
            UserOrganizationMembership.objects.get(
                user=user, organization=self.gos, role="Mentor"
            ).is_active
        )

        adult.mentor_active = True
        adult.save()

        self.assertFalse(
            UserOrganizationMembership.objects.get(
                user=user, organization=self.gos, role="Mentor"
            ).is_active
        )

    def test_existing_membership_is_not_duplicated(self):
        """A pre-existing membership row must not raise on profile save."""
        user = User.objects.create_user(
            username="preexisting", password="password123"  # nosec B106
        )
        UserOrganizationMembership.objects.create(
            user=user,
            organization=self.gos,
            role="Mentor",
            is_active=True,
        )
        Adult.objects.create(
            user=user,
            legal_first_name="Pre",
            last_name="Existing",
            is_mentor=True,
            organization=self.gos,
        )
        self.assertEqual(
            UserOrganizationMembership.objects.filter(user=user).count(), 1
        )

    def test_student_gets_membership(self):
        user = User.objects.create_user(
            username="student1", password="password123"  # nosec B106
        )
        Student.objects.create(
            user=user,
            legal_first_name="New",
            last_name="Student",
            organization=self.gos,
        )
        self.assertIn((self.gos.pk, "Student", True), self._memberships(user))

    def test_graduated_student_gets_inactive_membership(self):
        user = User.objects.create_user(
            username="student2", password="password123"  # nosec B106
        )
        Student.objects.create(
            user=user,
            legal_first_name="Grad",
            last_name="Student",
            organization=self.gos,
            graduated=True,
        )
        self.assertIn((self.gos.pk, "Student", False), self._memberships(user))

    def test_relationship_syncs_parent_membership(self):
        """Linking a student to an adult flags the adult as a parent through a
        queryset ``update()`` (bypassing post_save); the parent membership
        must still be created."""
        parent_user = User.objects.create_user(
            username="linked_parent", password="password123"  # nosec B106
        )
        adult = Adult.objects.create(
            user=parent_user,
            legal_first_name="Linked",
            last_name="Parent",
            is_parent=False,
            organization=self.gos,
        )
        student = Student.objects.create(
            legal_first_name="Kid",
            last_name="Student",
            organization=self.gos,
        )
        from programs.models import AdultStudentRelationship

        AdultStudentRelationship.objects.create(adult=adult, student=student)

        self.assertTrue(adult.is_parent)
        self.assertIn((self.gos.pk, "Parent", True), self._memberships(parent_user))


class BackfillProfileMembershipsTests(TestCase):
    """The backfill command is idempotent and mirrors the sync signal."""

    def setUp(self):
        self.gos = Organization.objects.get(slug="gos")

    def _memberships(self, user):
        return set(
            UserOrganizationMembership.objects.filter(user=user).values_list(
                "organization_id", "role", "is_active"
            )
        )

    def test_backfills_missing_memberships(self):
        user = User.objects.create_user(
            username="gap_user", password="password123"  # nosec B106
        )
        Adult.objects.create(
            user=user,
            legal_first_name="Gap",
            last_name="Mentor",
            is_mentor=True,
            mentor_active=False,
            organization=self.gos,
        )
        # Simulate the gap: profile exists, membership rows do not.
        UserOrganizationMembership.objects.filter(user=user).delete()

        call_command("backfill_profile_memberships", stdout=StringIO())

        self.assertEqual(self._memberships(user), {(self.gos.pk, "Mentor", False)})

    def test_backfill_is_idempotent(self):
        user = User.objects.create_user(
            username="gap_user2", password="password123"  # nosec B106
        )
        Adult.objects.create(
            user=user,
            legal_first_name="Gap",
            last_name="Parent",
            is_parent=True,
            organization=self.gos,
        )
        UserOrganizationMembership.objects.filter(user=user).delete()

        call_command("backfill_profile_memberships", stdout=StringIO())
        call_command("backfill_profile_memberships", stdout=StringIO())

        self.assertEqual(self._memberships(user), {(self.gos.pk, "Parent", True)})

    def test_backfill_skips_profiles_without_users(self):
        Adult.objects.create(
            legal_first_name="No",
            last_name="User",
            is_mentor=True,
            organization=self.gos,
        )
        call_command("backfill_profile_memberships", stdout=StringIO())
        self.assertEqual(UserOrganizationMembership.objects.count(), 0)

    def test_backfill_creates_student_membership(self):
        user = User.objects.create_user(
            username="gap_student", password="password123"  # nosec B106
        )
        Student.objects.create(
            user=user,
            legal_first_name="Gap",
            last_name="Student",
            organization=self.gos,
        )
        UserOrganizationMembership.objects.filter(user=user).delete()

        call_command("backfill_profile_memberships", stdout=StringIO())

        self.assertEqual(self._memberships(user), {(self.gos.pk, "Student", True)})
