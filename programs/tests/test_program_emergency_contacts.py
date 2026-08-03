from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from programs.models import (
    Adult,
    AdultStudentRelationship,
    Enrollment,
    Program,
    Student,
)


class ProgramEmergencyContactsTests(TestCase):
    def setUp(self):
        self.lead_mentor = User.objects.create_user(
            username="lead_mentor", password="password123"
        )  # nosec B106
        lm_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.lead_mentor.groups.add(lm_group)

        self.mentor = User.objects.create_user(
            username="mentor_user", password="password123"
        )  # nosec B106
        Adult.objects.create(
            user=self.mentor, first_name="Mentor", last_name="User", is_mentor=True
        )

        self.program = Program.objects.create(name="Test Program", active=True)

        self.active_student = Student.objects.create(
            first_name="Active",
            last_name="Student",
            personal_email="student@example.com",
            andrew_email="student@andrew.cmu.edu",
            phone_number="412-555-0100",
        )
        Enrollment.objects.create(
            student=self.active_student, program=self.program, active=True
        )

        self.inactive_student = Student.objects.create(
            first_name="Inactive",
            last_name="Student",
            personal_email="inactive@example.com",
        )
        Enrollment.objects.create(
            student=self.inactive_student, program=self.program, active=False
        )

        self.graduated_student = Student.objects.create(
            first_name="Graduated",
            last_name="Student",
            personal_email="graduated@example.com",
            graduated=True,
        )
        Enrollment.objects.create(
            student=self.graduated_student, program=self.program, active=True
        )

        self.parent_one = Adult.objects.create(
            first_name="Parent",
            last_name="One",
            is_parent=True,
            personal_email="parentone@example.com",
            phone_number="412-555-0101",
        )
        self.parent_two = Adult.objects.create(
            first_name="Parent",
            last_name="Two",
            is_parent=True,
            personal_email="parenttwo@example.com",
            phone_number="412-555-0102",
        )
        self.parent_three = Adult.objects.create(
            first_name="Parent",
            last_name="Three",
            is_parent=True,
            personal_email="parentthree@example.com",
            phone_number="412-555-0103",
        )
        AdultStudentRelationship.objects.create(
            adult=self.parent_one,
            student=self.active_student,
            relationship_to_student="parent",
        )
        AdultStudentRelationship.objects.create(
            adult=self.parent_two,
            student=self.active_student,
            relationship_to_student="parent",
        )
        AdultStudentRelationship.objects.create(
            adult=self.parent_three,
            student=self.active_student,
            relationship_to_student="guardian",
        )
        self.active_student.primary_contact = self.parent_one
        self.active_student.secondary_contact = self.parent_two
        self.active_student.save()

        # Parent and student users who must be blocked from the page
        self.parent_user = User.objects.create_user(
            username="parent_user", password="password123"
        )  # nosec B106
        self.parent_one.user = self.parent_user
        self.parent_one.save()

        self.student_user = User.objects.create_user(
            username="student_user", password="password123"
        )  # nosec B106
        self.active_student.user = self.student_user
        self.active_student.save()

    def _url(self):
        return reverse("program_emergency_contacts", args=[self.program.pk])

    def _login(self, username):
        self.client.login(username=username, password="password123")  # nosec B106

    def test_lead_mentor_can_view_page(self):
        self._login("lead_mentor")
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)

    def test_mentor_can_view_page(self):
        self._login("mentor_user")
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)

    def test_page_lists_active_student_with_contact_links(self):
        self._login("lead_mentor")
        response = self.client.get(self._url())
        self.assertContains(response, "Active Student")
        self.assertContains(response, 'href="mailto:student@example.com"')
        self.assertContains(response, 'href="mailto:student@andrew.cmu.edu"')
        self.assertContains(response, 'href="tel:412-555-0100"')

    def test_page_lists_parents_with_contact_links(self):
        self._login("lead_mentor")
        response = self.client.get(self._url())
        self.assertContains(response, "Parent One")
        self.assertContains(response, 'href="mailto:parentone@example.com"')
        self.assertContains(response, 'href="tel:412-555-0101"')
        self.assertContains(response, "Parent Two")
        self.assertContains(response, 'href="mailto:parenttwo@example.com"')
        self.assertContains(response, 'href="tel:412-555-0102"')

    def test_page_groups_guardians_into_columns(self):
        self._login("lead_mentor")
        response = self.client.get(self._url())
        self.assertContains(response, "Primary Guardian")
        self.assertContains(response, "Secondary Guardian")
        self.assertContains(response, "Other Contacts")
        self.assertContains(response, "Parent One")
        self.assertContains(response, "Parent Two")
        self.assertContains(response, "Parent Three")

    def test_page_renders_contact_buttons(self):
        self._login("lead_mentor")
        response = self.client.get(self._url())
        self.assertContains(
            response, 'class="btn btn-sm btn-outline-primary" href="tel:412-555-0100"'
        )
        self.assertContains(
            response,
            'class="btn btn-sm btn-outline-secondary" href="mailto:parentone@example.com"',
        )
        self.assertContains(
            response,
            'class="btn btn-sm btn-outline-secondary" href="mailto:parentthree@example.com"',
        )

    def test_page_excludes_inactive_and_graduated_students(self):
        self._login("lead_mentor")
        response = self.client.get(self._url())
        self.assertNotContains(response, "Inactive Student")
        self.assertNotContains(response, "Graduated Student")

    def test_parent_cannot_view_page(self):
        self._login("parent_user")
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 302)

    def test_student_cannot_view_page(self):
        self._login("student_user")
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 302)

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 302)

    def test_navbar_shows_emergency_contacts_instead_of_parents_anchor(self):
        self._login("lead_mentor")
        detail_url = reverse("program_detail", args=[self.program.pk])
        response = self.client.get(detail_url)
        ec_url = self._url()
        self.assertContains(response, f'href="{ec_url}"')
        self.assertContains(response, "Emergency Contacts")
        self.assertNotContains(response, "#parents")

    def test_navbar_shows_emergency_contacts_for_mentor(self):
        self._login("mentor_user")
        detail_url = reverse("program_detail", args=[self.program.pk])
        response = self.client.get(detail_url)
        ec_url = self._url()
        self.assertContains(response, f'href="{ec_url}"')
        self.assertContains(response, "Emergency Contacts")

    def test_navbar_hides_emergency_contacts_for_parent(self):
        self._login("parent_user")
        response = self.client.get(reverse("profile_dashboard"))
        ec_url = self._url()
        self.assertNotContains(response, f'href="{ec_url}"')
        self.assertNotContains(response, "Emergency Contacts")

    def test_navbar_hides_emergency_contacts_for_student(self):
        self._login("student_user")
        response = self.client.get(reverse("profile_dashboard"))
        ec_url = self._url()
        self.assertNotContains(response, f'href="{ec_url}"')
        self.assertNotContains(response, "Emergency Contacts")
