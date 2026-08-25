"""Tests for the reorganized navbar (grouped dropdowns instead of a long
flat list of links). See CHANGELOG.md for the user-facing summary.
"""

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from programs.models import (
    Enrollment,
    Program,
    ProgramFeature,
    Student,
)


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

    def test_applications_dropdown_groups_application_links(self):
        self._login()
        response = self.client.get(reverse("program_list"))
        self.assertContains(response, "Sliding Scale Applications")
        self.assertContains(response, "Guest Forms")
        self.assertContains(response, "Andrew IDs")


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
