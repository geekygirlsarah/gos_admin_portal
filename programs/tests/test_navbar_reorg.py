"""Tests for the reorganized navbar (grouped dropdowns instead of a long
flat list of links). See CHANGELOG.md for the user-facing summary.
"""

import unittest

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from programs.models import (
    Adult,
    AdultStudentRelationship,
    Enrollment,
    Program,
    ProgramFeature,
    Student,
)

try:
    from badges.models import Badge, StudentBadge
except ImportError:
    Badge = None
    StudentBadge = None


class LeadMentorAdminDropdownSplitTests(TestCase):
    """The old single "Admin" dropdown (~15 flat items) is now split into
    topical dropdowns so no single menu is overwhelming."""

    def setUp(self):
        self.lead_mentor = User.objects.create_user(
            username="lead_mentor", password="password123"
        )  # nosec B106
        lm_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.lead_mentor.groups.add(lm_group)

    def _login(self):
        self.client.login(username="lead_mentor", password="password123")  # nosec B106

    def test_admin_menu_is_split_into_topical_dropdowns(self):
        self._login()
        response = self.client.get(reverse("program_list"))
        # New topical dropdowns exist.
        self.assertContains(response, 'id="studentsAdminDropdown"')
        self.assertContains(response, 'id="adultsAdminDropdown"')
        self.assertContains(response, 'id="applicationsAdminDropdown"')
        self.assertContains(response, 'id="manageDataAdminDropdown"')
        self.assertContains(response, 'id="adminDropdown"')

    def test_redundant_all_programs_link_removed(self):
        self._login()
        response = self.client.get(reverse("program_list"))
        # "All Programs" duplicated the existing top-level "Programs" link
        # and has been removed from the Admin dropdown.
        self.assertNotContains(response, "All Programs")

    def test_students_dropdown_groups_student_admin_links(self):
        self._login()
        response = self.client.get(reverse("program_list"))
        self.assertContains(response, "All Students")
        self.assertContains(response, "Students by Grade")
        self.assertContains(response, "Students by School")
        self.assertContains(response, "Schools")

    def test_adults_dropdown_groups_adult_admin_links(self):
        self._login()
        response = self.client.get(reverse("program_list"))
        self.assertContains(response, "All Adults")
        self.assertContains(response, "All Parents")
        self.assertContains(response, "All Mentors")
        self.assertContains(response, "All Alumni")

    def test_applications_dropdown_groups_application_review_links(self):
        # "Applications" now only holds actual application-review
        # workflows; Guest Forms/Andrew IDs moved to "Manage Data" since
        # they're data-management tools, not application reviews.
        self._login()
        response = self.client.get(reverse("program_list"))
        self.assertContains(response, "Sliding Scale Applications")

    def test_manage_data_dropdown_groups_non_application_admin_tools(self):
        self._login()
        response = self.client.get(reverse("program_list"))
        self.assertContains(response, "Manage Guest Forms")
        self.assertContains(response, "Manage Andrew IDs")


class CurrentProgramDropdownTests(TestCase):
    """The current program's separate top-level links (name, students,
    outreach, badges, emergency contacts, dues owed, email) are now grouped
    into a single "[Program Name]" dropdown."""

    def setUp(self):
        self.lead_mentor = User.objects.create_user(
            username="lead_mentor", password="password123"
        )  # nosec B106
        lm_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.lead_mentor.groups.add(lm_group)
        self.program = Program.objects.create(name="Robotics 101", active=True)

    def test_program_links_grouped_into_single_dropdown(self):
        self.client.login(username="lead_mentor", password="password123")  # nosec B106
        response = self.client.get(reverse("program_detail", args=[self.program.pk]))
        self.assertContains(response, 'id="currentProgramDropdown"')
        self.assertContains(response, "Robotics 101")
        self.assertContains(response, "Overview")
        self.assertContains(response, "Emergency Contacts")
        self.assertContains(response, "Dues Owed")
        self.assertContains(response, "Email Program")


class StudentBadgesLinkNotDuplicatedTests(TestCase):
    """Regression test: students used to see the "Badges" nav link twice
    when their current program had the badges feature enabled."""

    def setUp(self):
        self.badges_feature = ProgramFeature.objects.get(key="badges")
        self.program = Program.objects.create(name="Test Program", active=True)
        self.program.features.add(self.badges_feature)

        self.student_user = User.objects.create_user(
            username="student1", password="password123"
        )  # nosec B106
        self.student = Student.objects.create(
            user=self.student_user, first_name="Alice", last_name="Zuberg"
        )
        Enrollment.objects.create(
            student=self.student, program=self.program, active=True
        )

    def test_badges_link_appears_only_once(self):
        self.client.login(username="student1", password="password123")  # nosec B106
        response = self.client.get(reverse("profile_dashboard"))
        nav_start = response.content.find(b'<nav class="navbar')
        nav_end = response.content.find(b"</nav>", nav_start)
        nav_content = response.content[nav_start:nav_end]
        self.assertEqual(nav_content.count(b">Badges<"), 1)


class PreviouslyOrphanedPagesNowLinkedTests(TestCase):
    """These pages existed with no link anywhere in the app (not even a
    button on another page) prior to this fix; they're now reachable from
    the Lead Mentor dropdowns."""

    def setUp(self):
        self.lead_mentor = User.objects.create_user(
            username="lead_mentor", password="password123"
        )  # nosec B106
        lm_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.lead_mentor.groups.add(lm_group)

    def _login(self):
        self.client.login(username="lead_mentor", password="password123")  # nosec B106

    def test_new_program_link_added_to_admin_dropdown(self):
        self._login()
        response = self.client.get(reverse("program_list"))
        self.assertContains(response, reverse("program_create"))
        self.assertContains(response, "New Program")

    def test_import_dashboard_link_added_to_admin_dropdown(self):
        self._login()
        response = self.client.get(reverse("program_list"))
        self.assertContains(response, reverse("import_dashboard"))
        self.assertContains(response, "Import Data")

    def test_all_student_photos_link_added_to_students_dropdown(self):
        self._login()
        response = self.client.get(reverse("program_list"))
        self.assertContains(response, reverse("student_photos"))
        self.assertContains(response, "All Student Photos")


class StudentDashboardAndCarpoolMapLinkTests(TestCase):
    """The "Carpool Map" page previously had no navbar link for Students
    (only a button on the Dashboard); it's now reachable from its own
    standalone "Carpool Map" navbar dropdown. "Dashboard" is now also a
    top-level link for Students instead of being tucked away in the account
    menu only."""

    def setUp(self):
        self.program = Program.objects.create(name="Test Program", active=True)
        self.student_user = User.objects.create_user(
            username="student1", password="password123"
        )  # nosec B106
        self.student = Student.objects.create(
            user=self.student_user, first_name="Alice", last_name="Zuberg"
        )
        Enrollment.objects.create(
            student=self.student, program=self.program, active=True
        )

    def _login(self):
        self.client.login(username="student1", password="password123")  # nosec B106

    def test_dashboard_link_shown_for_student(self):
        self._login()
        response = self.client.get(reverse("profile_dashboard"))
        self.assertContains(response, reverse("profile_dashboard"))
        self.assertContains(response, "Dashboard")

    def test_carpool_map_link_shown_for_student(self):
        self._login()
        response = self.client.get(reverse("profile_dashboard"))
        map_url = reverse("program_student_map", args=[self.program.pk])
        self.assertContains(response, map_url)
        self.assertContains(response, "Carpool Map")


class ParentDashboardAndProgramDropdownTests(TestCase):
    """Parents already have permission to read a program's Outreach events
    and view the Carpool Map (the Dashboard already surfaces both), but the
    navbar's program dropdown left Parents out of the Outreach link, and
    there was no Carpool Map link at all. Outreach now shows in the current
    program dropdown, Carpool Map is its own standalone dropdown, and
    "Dashboard" is a top-level link like it is for Students."""

    def setUp(self):
        self.outreach_feature = ProgramFeature.objects.get(key="outreach")
        self.program = Program.objects.create(name="Robotics 101", active=True)
        self.program.features.add(self.outreach_feature)

        self.parent_user = User.objects.create_user(
            username="parent_user", password="password123"
        )  # nosec B106
        self.parent_adult = Adult.objects.create(
            user=self.parent_user, first_name="Parent", last_name="One", is_parent=True
        )
        self.child = Student.objects.create(first_name="Child", last_name="One")
        AdultStudentRelationship.objects.create(
            adult=self.parent_adult,
            student=self.child,
            relationship_to_student="parent",
        )
        Enrollment.objects.create(student=self.child, program=self.program, active=True)

    def _login(self):
        self.client.login(username="parent_user", password="password123")  # nosec B106

    def test_dashboard_link_shown_for_parent(self):
        self._login()
        response = self.client.get(reverse("profile_dashboard"))
        self.assertContains(response, reverse("profile_dashboard"))
        self.assertContains(response, "Dashboard")

    def test_outreach_link_shown_in_program_dropdown_for_parent(self):
        self._login()
        response = self.client.get(reverse("profile_dashboard"))
        outreach_url = reverse("outreach:event_list", args=[self.program.pk])
        self.assertContains(response, outreach_url)
        self.assertContains(response, "Outreach")

    def test_carpool_map_link_shown_for_parent(self):
        self._login()
        response = self.client.get(reverse("profile_dashboard"))
        map_url = reverse("program_student_map", args=[self.program.pk])
        self.assertContains(response, map_url)
        self.assertContains(response, "Carpool Map")


class CarpoolMapStandaloneDropdownTests(TestCase):
    """ "Carpool Map" is its own top-level navbar dropdown (instead of a
    scattered/context-dependent link), listing only the Student's/Parent's
    currently-active (not past, not inactive) programs as menu items."""

    def setUp(self):
        self.past_program = Program.objects.create(name="Past Program", active=False)
        self.current_program = Program.objects.create(
            name="Current Program", active=True
        )
        self.second_current_program = Program.objects.create(
            name="Second Current Program", active=True
        )
        self.student_user = User.objects.create_user(
            username="student1", password="password123"
        )  # nosec B106
        self.student = Student.objects.create(
            user=self.student_user, first_name="Alice", last_name="Zuberg"
        )
        Enrollment.objects.create(
            student=self.student, program=self.past_program, active=False
        )
        Enrollment.objects.create(
            student=self.student, program=self.current_program, active=True
        )
        Enrollment.objects.create(
            student=self.student, program=self.second_current_program, active=True
        )

    def _login(self):
        self.client.login(username="student1", password="password123")  # nosec B106

    def _nav_content(self, response):
        nav_start = response.content.find(b'<nav class="navbar')
        nav_end = response.content.find(b"</nav>", nav_start)
        return response.content[nav_start:nav_end]

    def test_carpool_map_is_a_standalone_dropdown(self):
        self._login()
        response = self.client.get(reverse("profile_dashboard"))
        nav_content = self._nav_content(response)
        self.assertIn(b'id="carpoolMapDropdown"', nav_content)
        self.assertIn(b">Carpool Map<", nav_content)

    def test_carpool_map_dropdown_lists_only_currently_active_programs(self):
        self._login()
        response = self.client.get(reverse("profile_dashboard"))
        nav_content = self._nav_content(response)
        self.assertIn(
            reverse("program_student_map", args=[self.current_program.pk]).encode(),
            nav_content,
        )
        self.assertIn(
            reverse(
                "program_student_map", args=[self.second_current_program.pk]
            ).encode(),
            nav_content,
        )
        self.assertNotIn(
            reverse("program_student_map", args=[self.past_program.pk]).encode(),
            nav_content,
        )

    def test_carpool_map_dropdown_shows_program_names_as_menu_items(self):
        self._login()
        response = self.client.get(reverse("profile_dashboard"))
        nav_content = self._nav_content(response)
        self.assertIn(b"Current Program", nav_content)
        self.assertIn(b"Second Current Program", nav_content)
        self.assertNotIn(b"Past Program", nav_content)

    def test_carpool_map_dropdown_hidden_when_no_active_enrollments(self):
        self.student.enrollment_set.update(active=False)
        self._login()
        response = self.client.get(reverse("profile_dashboard"))
        nav_content = self._nav_content(response)
        self.assertNotIn(b"carpoolMapDropdown", nav_content)


class NavbarBadgeCountTests(TestCase):
    """The navbar Badges link should show a count of earned badges and
    should be visible whenever the user either has earned badges or is
    enrolled in a program with the badges feature — not just when the
    *current* program has badges."""

    def setUp(self):
        self.badges_feature = ProgramFeature.objects.get(key="badges")
        self.program = Program.objects.create(name="Badge Program", active=True)
        self.program.features.add(self.badges_feature)

        self.other_program = Program.objects.create(
            name="Other Program", active=True
        )

        self.student_user = User.objects.create_user(
            username="student1", password="password123"
        )  # nosec B106
        self.student = Student.objects.create(
            user=self.student_user, first_name="Alice", last_name="Zuberg"
        )
        Enrollment.objects.create(
            student=self.student, program=self.program, active=True
        )

        self.parent_user = User.objects.create_user(
            username="parent1", password="password123"
        )  # nosec B106
        self.parent_adult = Adult.objects.create(
            user=self.parent_user,
            first_name="Parent",
            last_name="One",
            is_parent=True,
        )
        AdultStudentRelationship.objects.create(
            adult=self.parent_adult,
            student=self.student,
            relationship_to_student="parent",
        )

    def _nav_content(self, response):
        nav_start = response.content.find(b'<nav class="navbar')
        nav_end = response.content.find(b"</nav>", nav_start)
        return response.content[nav_start:nav_end]

    def _nav_contains_badges_link(self, nav_content):
        return b"Badges" in nav_content

    @unittest.skipIf(Badge is None, "badges app not installed")
    def test_student_sees_badge_count_when_has_earned_badges(self):
        badge = Badge.objects.create(name="Soldering", level=1)
        StudentBadge.objects.create(student=self.student, badge=badge)
        self.client.login(username="student1", password="password123")  # nosec B106
        response = self.client.get(reverse("profile_dashboard"))
        nav_content = self._nav_content(response)
        self.assertIn(b"Badges", nav_content)
        self.assertIn(b"badge", nav_content)
        self.assertIn(b"1", nav_content)

    def test_student_sees_badges_link_when_enrolled_in_badge_program(self):
        self.client.login(username="student1", password="password123")  # nosec B106
        response = self.client.get(reverse("profile_dashboard"))
        nav_content = self._nav_content(response)
        self.assertIn(b"Badges", nav_content)

    def test_student_no_badges_link_when_no_badge_program(self):
        self.student.enrollment_set.all().delete()
        Enrollment.objects.create(
            student=self.student, program=self.other_program, active=True
        )
        self.client.login(username="student1", password="password123")  # nosec B106
        response = self.client.get(reverse("profile_dashboard"))
        # The main nav area (before the mobile/account sections) should not
        # contain a "Badges" link when no program has the feature.
        main_nav_start = response.content.find(b'<ul class="navbar-nav')
        main_nav_end = response.content.find(b'</ul>', main_nav_start)
        main_nav = response.content[main_nav_start:main_nav_end]
        self.assertNotIn(b"Badges", main_nav)

    @unittest.skipIf(Badge is None, "badges app not installed")
    def test_parent_sees_child_badge_count(self):
        badge = Badge.objects.create(name="Design", level=1)
        StudentBadge.objects.create(student=self.student, badge=badge)
        self.client.login(username="parent1", password="password123")  # nosec B106
        response = self.client.get(reverse("profile_dashboard"))
        nav_content = self._nav_content(response)
        self.assertIn(b"Badges", nav_content)
        self.assertIn(b"1", nav_content)

    def test_parent_sees_badges_link_when_child_in_badge_program(self):
        self.client.login(username="parent1", password="password123")  # nosec B106
        response = self.client.get(reverse("profile_dashboard"))
        nav_content = self._nav_content(response)
        self.assertIn(b"Badges", nav_content)

    def test_student_badge_count_reflects_multiple_earned(self):
        if Badge is None:
            self.skipTest("badges app not installed")
        badge1 = Badge.objects.create(name="Soldering", level=1)
        badge2 = Badge.objects.create(name="CAD", level=1)
        StudentBadge.objects.create(student=self.student, badge=badge1)
        StudentBadge.objects.create(student=self.student, badge=badge2)
        self.client.login(username="student1", password="password123")  # nosec B106
        response = self.client.get(reverse("profile_dashboard"))
        nav_content = self._nav_content(response)
        self.assertIn(b"2", nav_content)

    @unittest.skipIf(Badge is None, "badges app not installed")
    def test_student_with_earned_badges_sees_link_even_without_badge_program(self):
        """A student who earned badges in a past program should still see
        the Badges link in the main nav even if their current program
        doesn't have the badges feature."""
        self.student.enrollment_set.all().delete()
        Enrollment.objects.create(
            student=self.student, program=self.other_program, active=True
        )
        badge = Badge.objects.create(name="Soldering", level=1)
        StudentBadge.objects.create(student=self.student, badge=badge)
        self.client.login(username="student1", password="password123")  # nosec B106
        response = self.client.get(reverse("profile_dashboard"))
        main_nav_start = response.content.find(b'<ul class="navbar-nav')
        main_nav_end = response.content.find(b'</ul>', main_nav_start)
        main_nav = response.content[main_nav_start:main_nav_end]
        self.assertIn(b"Badges", main_nav)
        self.assertIn(b"1", main_nav)
