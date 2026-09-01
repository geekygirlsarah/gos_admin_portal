from cryptography.fernet import Fernet
from django.contrib.auth.models import Group, User
from django.test import TestCase, override_settings
from django.urls import reverse

from programs.models import Adult, Enrollment, Program, Student

TEST_FILE_KEY = Fernet.generate_key().decode()


@override_settings(FILE_ENCRYPTION_KEY=TEST_FILE_KEY)
class ProgramMedicalInfoTests(TestCase):
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
            user=self.mentor,
            legal_first_name="Mentor",
            last_name="User",
            is_mentor=True,
        )

        self.program = Program.objects.create(name="Test Program", active=True)

        self.student_with_allergies = Student.objects.create(
            preferred_first_name="Allergy",
            last_name="Student",
            allergies="Peanuts",
        )
        Enrollment.objects.create(
            student=self.student_with_allergies, program=self.program, active=True
        )

        self.student_with_dietary = Student.objects.create(
            preferred_first_name="Dietary",
            last_name="Student",
            dietary_restrictions="Vegetarian",
        )
        Enrollment.objects.create(
            student=self.student_with_dietary, program=self.program, active=True
        )

        self.student_with_notes = Student.objects.create(
            preferred_first_name="Notes",
            last_name="Student",
            medical_notes="Asthma",
        )
        Enrollment.objects.create(
            student=self.student_with_notes, program=self.program, active=True
        )

        self.student_no_info = Student.objects.create(
            preferred_first_name="No",
            last_name="Info",
        )
        Enrollment.objects.create(
            student=self.student_no_info, program=self.program, active=True
        )

        self.inactive_student = Student.objects.create(
            preferred_first_name="Inactive",
            last_name="Student",
            allergies="Shellfish",
        )
        Enrollment.objects.create(
            student=self.inactive_student, program=self.program, active=False
        )

        self.graduated_student = Student.objects.create(
            preferred_first_name="Graduated",
            last_name="Student",
            allergies="Lactose",
            graduated=True,
        )
        Enrollment.objects.create(
            student=self.graduated_student, program=self.program, active=True
        )

        # Parent and student users who must be blocked from the page
        self.parent_user = User.objects.create_user(
            username="parent_user", password="password123"
        )  # nosec B106
        Adult.objects.create(
            user=self.parent_user,
            legal_first_name="Parent",
            last_name="User",
            is_parent=True,
        )

        self.student_user = User.objects.create_user(
            username="student_user", password="password123"
        )  # nosec B106
        self.student_with_allergies.user = self.student_user
        self.student_with_allergies.save()

    def _url(self):
        return reverse("program_medical_info", args=[self.program.pk])

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

    def test_page_lists_students_with_medical_info(self):
        self._login("lead_mentor")
        response = self.client.get(self._url())
        self.assertContains(response, "Allergy Student")
        self.assertContains(response, "Peanuts")
        self.assertContains(response, "Dietary Student")
        self.assertContains(response, "Vegetarian")
        self.assertContains(response, "Notes Student")
        self.assertContains(response, "Asthma")

    def test_page_excludes_student_without_info(self):
        self._login("lead_mentor")
        response = self.client.get(self._url())
        self.assertNotContains(response, "No Info")

    def test_page_excludes_inactive_and_graduated_students(self):
        self._login("lead_mentor")
        response = self.client.get(self._url())
        self.assertNotContains(response, "Inactive Student")
        self.assertNotContains(response, "Graduated Student")

    def test_page_excludes_student_with_blank_strings_only(self):
        Student.objects.create(
            preferred_first_name="Blank",
            last_name="Info",
            allergies="",
            dietary_restrictions="",
            medical_notes="",
        )
        enrollment = Enrollment.objects.create(
            student=Student.objects.get(preferred_first_name="Blank"),
            program=self.program,
            active=True,
        )
        self._login("lead_mentor")
        response = self.client.get(self._url())
        self.assertNotContains(response, "Blank Info")

    def test_page_links_to_student_detail(self):
        self._login("lead_mentor")
        response = self.client.get(self._url())
        self.assertContains(
            response, reverse("student_detail", args=[self.student_with_allergies.pk])
        )

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

    def test_navbar_shows_medical_info_for_mentor(self):
        self._login("mentor_user")
        detail_url = reverse("program_detail", args=[self.program.pk])
        response = self.client.get(detail_url)
        med_url = self._url()
        self.assertContains(response, f'href="{med_url}"')
        self.assertContains(response, "Medical Info")

    def test_navbar_hides_medical_info_for_parent(self):
        self._login("parent_user")
        response = self.client.get(reverse("profile_dashboard"))
        med_url = self._url()
        self.assertNotContains(response, f'href="{med_url}"')
        self.assertNotContains(response, "Medical Info")

    def test_navbar_hides_medical_info_for_student(self):
        self._login("student_user")
        response = self.client.get(reverse("profile_dashboard"))
        med_url = self._url()
        self.assertNotContains(response, f'href="{med_url}"')
        self.assertNotContains(response, "Medical Info")
