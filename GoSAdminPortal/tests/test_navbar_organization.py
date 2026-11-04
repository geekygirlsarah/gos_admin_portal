from django.contrib.auth.models import Group, User
from django.db import connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from badges.models import Badge, StudentBadge
from GoSAdminPortal.context_processors import navbar_context
from organizations.models import Organization, UserOrganizationMembership
from programs.models import (
    Adult,
    AdultStudentRelationship,
    Enrollment,
    Program,
    ProgramFeature,
    Student,
)
from programs.permission_views import get_user_role


class NavbarOrganizationTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.get(slug="gos")
        self.other = Organization.objects.create(name="Other", slug="other")
        self.user = User.objects.create_user(username="navbar-user")
        self.local = Program.objects.create(organization=self.org, name="Local")
        self.foreign = Program.objects.create(organization=self.other, name="Foreign")

    def context(self, path="/", organization=True):
        request = RequestFactory().get(path)
        request.user = User.objects.get(pk=self.user.pk)
        request.organization = self.org if organization else None
        return navbar_context(request)

    def student(self, organization, user=None):
        return Student.objects.create(
            organization=organization,
            user=user,
            legal_first_name="Child",
            last_name="Test",
        )

    def feature(self, organization, program, key):
        feature, _ = ProgramFeature.objects.get_or_create(
            organization=organization, key=key, defaults={"name": key}
        )
        program.features.add(feature)

    def test_student_programs_and_badges_are_scoped_on_every_related_record(self):
        student = self.student(self.org, self.user)
        wrong_enrollment = Program.objects.create(organization=self.org, name="Wrong")
        for org, program in (
            (self.org, self.local),
            (self.org, self.foreign),
            (self.other, wrong_enrollment),
        ):
            Enrollment.objects.create(
                organization=org, student=student, program=program
            )
            for key in ("outreach", "orders", "travel", "badges"):
                self.feature(program.organization, program, key)
        badge = Badge.objects.create(organization=self.org, name="Local badge")
        other_badge = Badge.objects.create(organization=self.other, name="Other badge")
        StudentBadge.objects.create(organization=self.org, student=student, badge=badge)
        StudentBadge.objects.create(
            organization=self.other, student=student, badge=badge
        )
        StudentBadge.objects.create(
            organization=self.org, student=student, badge=other_badge
        )

        context = self.context()
        for key in (
            "navbar_student_programs",
            "carpool_map_programs",
            "student_outreach_programs",
            "student_orders_programs",
            "student_travel_programs",
        ):
            with self.subTest(key=key):
                self.assertEqual(context[key], [self.local])
        self.assertEqual(context["current_program"], self.local)
        self.assertEqual(context["navbar_badge_count"], 1)
        self.assertTrue(context["user_has_any_badge_program"])

    def test_parent_links_and_enrollments_require_consistent_organizations(self):
        parent = Adult.objects.create(
            organization=self.org,
            user=self.user,
            legal_first_name="Parent",
            last_name="Test",
            is_parent=True,
        )
        local_child = self.student(self.org)
        foreign_child = self.student(self.other)
        wrong_link = self.student(self.org)
        for org, child in (
            (self.org, local_child),
            (self.org, foreign_child),
            (self.other, wrong_link),
        ):
            AdultStudentRelationship.objects.create(
                organization=org, adult=parent, student=child
            )
        for child, program in (
            (local_child, self.local),
            (local_child, self.foreign),
            (foreign_child, self.foreign),
            (wrong_link, self.foreign),
        ):
            Enrollment.objects.create(
                organization=self.org, student=child, program=program
            )
        self.feature(self.org, self.local, "travel")
        self.feature(self.org, self.local, "badges")
        self.feature(self.other, self.foreign, "travel")
        badge = Badge.objects.create(organization=self.org, name="Local")
        StudentBadge.objects.create(
            organization=self.org, student=local_child, badge=badge
        )
        StudentBadge.objects.create(
            organization=self.org, student=foreign_child, badge=badge
        )
        StudentBadge.objects.create(
            organization=self.org, student=wrong_link, badge=badge
        )

        context = self.context()
        self.assertEqual(context["carpool_map_programs"], [self.local])
        self.assertEqual(context["parent_travel_programs"], [self.local])
        self.assertEqual(context["current_program"], self.local)
        self.assertEqual(context["navbar_badge_count"], 1)
        self.assertTrue(context["user_has_any_badge_program"])

    def test_foreign_features_do_not_enable_navigation(self):
        student = self.student(self.org, self.user)
        Enrollment.objects.create(
            organization=self.org, student=student, program=self.local
        )
        for key in ("outreach", "orders", "travel", "badges"):
            self.feature(self.other, self.local, key)
        context = self.context()
        self.assertEqual(context["carpool_map_programs"], [self.local])
        for key in (
            "student_outreach_programs",
            "student_orders_programs",
            "student_travel_programs",
        ):
            self.assertEqual(context[key], [])
        self.assertFalse(context["user_has_any_badge_program"])
        for key in ("badges", "outreach", "orders", "travel"):
            self.assertFalse(context[f"current_program_has_{key}"])

    def test_foreign_profiles_and_missing_organization_fail_closed(self):
        student = self.student(self.other, self.user)
        Enrollment.objects.create(
            organization=self.other, student=student, program=self.foreign
        )
        badge = Badge.objects.create(organization=self.other, name="Foreign")
        StudentBadge.objects.create(
            organization=self.other, student=student, badge=badge
        )
        for organization in (True, False):
            with self.subTest(organization=organization):
                context = self.context(organization=organization)
                self.assertEqual(context["carpool_map_programs"], [])
                self.assertEqual(context["navbar_badge_count"], 0)
                self.assertIsNone(context["current_program"])

    def test_missing_organization_hides_local_data_too(self):
        student = self.student(self.org, self.user)
        Enrollment.objects.create(
            organization=self.org, student=student, program=self.local
        )
        self.feature(self.org, self.local, "badges")
        badge = Badge.objects.create(organization=self.org, name="Local")
        StudentBadge.objects.create(organization=self.org, student=student, badge=badge)
        context = self.context(organization=False)
        self.assertIsNone(context["current_program"])
        self.assertEqual(context["carpool_map_programs"], [])
        self.assertEqual(context["navbar_badge_count"], 0)
        self.assertFalse(context["user_has_any_badge_program"])

    def test_foreign_adult_cannot_supply_parent_flags_or_children(self):
        parent = Adult.objects.create(
            organization=self.other,
            user=self.user,
            legal_first_name="Parent",
            last_name="Test",
            is_parent=True,
            is_mentor=True,
            is_alumni=True,
        )
        child = self.student(self.org)
        AdultStudentRelationship.objects.create(
            organization=self.org, adult=parent, student=child
        )
        Enrollment.objects.create(
            organization=self.org, student=child, program=self.local
        )
        badge = Badge.objects.create(organization=self.org, name="Local")
        StudentBadge.objects.create(organization=self.org, student=child, badge=badge)
        grouped_context = self.context()
        self.assertEqual(grouped_context["carpool_map_programs"], [])
        self.assertEqual(grouped_context["navbar_badge_count"], 0)
        self.user.groups.clear()
        context = self.context()
        self.assertFalse(context["navbar_is_parent"])
        self.assertFalse(context["navbar_is_mentor"])
        self.assertFalse(context["navbar_is_alumni"])
        self.assertEqual(context["carpool_map_programs"], [])

    def test_role_uses_active_organization(self):
        UserOrganizationMembership.objects.create(
            user=self.user, organization=self.org, role="Student"
        )
        UserOrganizationMembership.objects.create(
            user=self.user, organization=self.other, role="Mentor"
        )
        self.assertEqual(self.context()["navbar_role"], "Student")
        request = RequestFactory().get("/")
        request.user = User.objects.get(pk=self.user.pk)
        request.organization = self.other
        self.assertEqual(navbar_context(request)["navbar_role"], "Mentor")

    def test_foreign_roles_and_global_groups_do_not_grant_local_flags(self):
        UserOrganizationMembership.objects.create(
            user=self.user, organization=self.org, role="Staff"
        )
        for role in ("Parent", "Mentor", "Alumni"):
            UserOrganizationMembership.objects.create(
                user=self.user, organization=self.other, role=role
            )
            group, _ = Group.objects.get_or_create(name=role)
            self.user.groups.add(group)

        local = self.context()
        self.assertEqual(local["navbar_role"], "Staff")
        for role in ("parent", "mentor", "alumni"):
            self.assertFalse(local[f"navbar_is_{role}"])

        request = RequestFactory().get("/")
        request.user = User.objects.get(pk=self.user.pk)
        request.organization = self.other
        foreign = navbar_context(request)
        self.assertEqual(foreign["navbar_role"], "Mentor")
        for role in ("parent", "mentor", "alumni"):
            self.assertTrue(foreign[f"navbar_is_{role}"])

    def test_inactive_memberships_override_profile_flags_and_groups(self):
        parent = Adult.objects.create(
            organization=self.org,
            user=self.user,
            legal_first_name="Parent",
            last_name="Test",
            is_parent=True,
            is_mentor=True,
            mentor_active=True,
            is_alumni=True,
        )
        child = self.student(self.org)
        AdultStudentRelationship.objects.create(
            organization=self.org, adult=parent, student=child
        )
        Enrollment.objects.create(
            organization=self.org, student=child, program=self.local
        )
        badge = Badge.objects.create(organization=self.org, name="Local")
        StudentBadge.objects.create(organization=self.org, student=child, badge=badge)
        UserOrganizationMembership.objects.filter(
            user=self.user, organization=self.org
        ).update(is_active=False)
        UserOrganizationMembership.objects.create(
            user=self.user, organization=self.org, role="Staff"
        )
        for role in ("Parent", "Mentor", "Alumni"):
            group, _ = Group.objects.get_or_create(name=role)
            self.user.groups.add(group)

        context = self.context()
        self.assertEqual(context["navbar_role"], "Staff")
        for role in ("parent", "mentor", "alumni"):
            self.assertFalse(context[f"navbar_is_{role}"])
        self.assertEqual(context["carpool_map_programs"], [])
        self.assertEqual(context["navbar_badge_count"], 0)

    def test_multi_role_memberships_enable_each_flag_without_profile(self):
        for role in ("Parent", "Mentor", "Alumni"):
            UserOrganizationMembership.objects.create(
                user=self.user, organization=self.org, role=role
            )
        context = self.context()
        self.assertEqual(context["navbar_role"], "Mentor")
        for role in ("parent", "mentor", "alumni"):
            self.assertTrue(context[f"navbar_is_{role}"])
        missing = self.context(organization=False)
        self.assertIsNone(missing["navbar_role"])
        for role in ("parent", "mentor", "alumni"):
            self.assertFalse(missing[f"navbar_is_{role}"])

    def test_primary_role_priority_matches_shared_role_resolver(self):
        for role in ("Staff", "Student", "Alumni", "Parent", "Mentor", "LeadMentor"):
            UserOrganizationMembership.objects.create(
                user=self.user, organization=self.org, role=role
            )
            self.assertEqual(self.context()["navbar_role"], role)
            self.assertEqual(
                self.context()["navbar_role"], get_user_role(self.user, org=self.org)
            )

    def test_multi_role_rendered_navigation_reflects_revocation(self):
        Adult.objects.create(
            organization=self.org,
            user=self.user,
            legal_first_name="Multi-role",
            last_name="Test",
            is_parent=True,
            is_mentor=True,
            mentor_active=True,
            is_alumni=True,
        )
        self.client.force_login(self.user)
        response = self.client.get(reverse("profile_dashboard"))
        self.assertContains(response, f'href="{reverse("parent_payments")}"')
        self.assertContains(response, 'id="attendanceDropdown"')

        UserOrganizationMembership.objects.filter(
            user=self.user, organization=self.org, role="Parent"
        ).update(is_active=False)
        response = self.client.get(reverse("profile_dashboard"))
        self.assertNotContains(response, f'href="{reverse("parent_payments")}"')
        self.assertContains(response, 'id="attendanceDropdown"')

    def test_legacy_roles_and_missing_organization_fail_closed(self):
        Adult.objects.create(
            organization=self.org,
            user=self.user,
            legal_first_name="Legacy",
            last_name="Test",
            is_parent=True,
            is_mentor=True,
            mentor_active=True,
            is_alumni=True,
        )
        UserOrganizationMembership.objects.filter(user=self.user).delete()
        group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.user.groups.add(group)
        for organization in (True, False):
            with self.subTest(organization=organization):
                context = self.context(organization=organization)
                self.assertIsNone(context["navbar_role"])
                for role in ("parent", "mentor", "alumni", "student"):
                    self.assertFalse(context[f"navbar_is_{role}"])

    def test_superuser_keeps_lead_role_and_scoped_active_flags(self):
        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])
        for role, organization, is_active in (
            ("Parent", self.org, True),
            ("Mentor", self.org, False),
            ("Alumni", self.other, True),
        ):
            UserOrganizationMembership.objects.create(
                user=self.user,
                organization=organization,
                role=role,
                is_active=is_active,
            )
        context = self.context()
        self.assertEqual(context["navbar_role"], "LeadMentor")
        self.assertTrue(context["navbar_is_parent"])
        self.assertFalse(context["navbar_is_mentor"])
        self.assertFalse(context["navbar_is_alumni"])
        missing = self.context(organization=False)
        self.assertEqual(missing["navbar_role"], "LeadMentor")
        for role in ("parent", "mentor", "alumni"):
            self.assertFalse(missing[f"navbar_is_{role}"])

    def test_membership_queries_do_not_grow_with_roles_or_organizations(self):
        UserOrganizationMembership.objects.create(
            user=self.user, organization=self.org, role="Staff"
        )
        with CaptureQueriesContext(connection) as initial:
            self.context()
        for role in ("Parent", "Mentor", "Alumni"):
            for org in (self.org, self.other):
                UserOrganizationMembership.objects.create(
                    user=self.user, organization=org, role=role
                )
        with CaptureQueriesContext(connection) as expanded:
            context = self.context()
        for role in ("parent", "mentor", "alumni"):
            self.assertTrue(context[f"navbar_is_{role}"])

        def membership_queries(queries):
            return [
                query
                for query in queries
                if "organizations_userorganizationmembership" in query["sql"]
            ]

        self.assertEqual(len(membership_queries(initial)), 1)
        self.assertEqual(
            len(membership_queries(initial)), len(membership_queries(expanded))
        )

    def test_inactive_enrollments_do_not_enable_lists_or_badges(self):
        student = self.student(self.org, self.user)
        Enrollment.objects.create(
            organization=self.org, student=student, program=self.local, active=False
        )
        self.feature(self.org, self.local, "badges")
        self.feature(self.org, self.local, "travel")
        context = self.context()
        self.assertEqual(context["current_program"], self.local)
        self.assertEqual(context["carpool_map_programs"], [])
        self.assertEqual(context["student_travel_programs"], [])
        self.assertFalse(context["user_has_any_badge_program"])

    def test_url_program_and_alumni_history_are_scoped(self):
        student = self.student(self.other)
        Adult.objects.create(
            organization=self.org,
            user=self.user,
            legal_first_name="Alumni",
            last_name="Test",
            is_alumni=True,
            student_record=student,
        )
        Enrollment.objects.create(
            organization=self.org, student=student, program=self.local
        )
        self.assertIsNone(self.context()["current_program"])
        self.assertIsNone(
            self.context(f"/programs/{self.foreign.pk}/")["current_program"]
        )
        self.assertEqual(
            self.context(f"/programs/{self.local.pk}/")["current_program"], self.local
        )

    def test_student_navigation_query_count_does_not_grow_per_program(self):
        student = self.student(self.org, self.user)
        Enrollment.objects.create(
            organization=self.org, student=student, program=self.local
        )
        with CaptureQueriesContext(connection) as initial:
            self.context()
        for index in range(5):
            program = Program.objects.create(
                organization=self.org, name=f"Program {index}"
            )
            Enrollment.objects.create(
                organization=self.org, student=student, program=program
            )
        with CaptureQueriesContext(connection) as expanded:
            self.context()
        self.assertLessEqual(len(expanded), len(initial) + 1)
