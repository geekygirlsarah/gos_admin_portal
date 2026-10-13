"""Lead mentors should be able to mark a student inactive in a program (rather
than flat-out removing the enrollment) and later mark them active again.
"""

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from programs.models import Enrollment, Program, Student


class ProgramStudentActiveToggleTests(TestCase):
    def setUp(self):
        self.password = "test_pass_123"  # nosec B105
        self.user = User.objects.create_user(
            username="leadmentor", password=self.password
        )
        self.lead_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.user.groups.add(self.lead_group)
        self.client.login(username="leadmentor", password=self.password)
        self.program = Program.objects.create(name="FLL 2026")
        self.student = Student.objects.create(
            legal_first_name="Active", last_name="Student"
        )
        self.enrollment = Enrollment.objects.create(
            student=self.student, program=self.program, active=True
        )

    def test_deactivate_marks_enrollment_inactive(self):
        url = reverse(
            "program_student_deactivate", args=[self.program.pk, self.student.pk]
        )
        response = self.client.post(url)
        self.assertRedirects(
            response, reverse("program_detail", args=[self.program.pk])
        )
        self.enrollment.refresh_from_db()
        self.assertFalse(self.enrollment.active)
        self.assertTrue(
            Enrollment.objects.filter(
                student=self.student, program=self.program
            ).exists(),
            "Deactivation must keep the enrollment record.",
        )

    def test_activate_marks_enrollment_active(self):
        self.enrollment.active = False
        self.enrollment.save(update_fields=["active"])
        url = reverse(
            "program_student_activate", args=[self.program.pk, self.student.pk]
        )
        response = self.client.post(url)
        self.assertRedirects(
            response, reverse("program_detail", args=[self.program.pk])
        )
        self.enrollment.refresh_from_db()
        self.assertTrue(self.enrollment.active)

    def test_deactivate_only_affects_target_program(self):
        other_program = Program.objects.create(name="Other")
        other_enrollment = Enrollment.objects.create(
            student=self.student, program=other_program, active=True
        )
        url = reverse(
            "program_student_deactivate", args=[self.program.pk, self.student.pk]
        )
        self.client.post(url)
        other_enrollment.refresh_from_db()
        self.assertTrue(other_enrollment.active)

    def test_detail_shows_mark_inactive_for_active_student(self):
        response = self.client.get(reverse("program_detail", args=[self.program.pk]))
        self.assertContains(response, "Mark Inactive")
        deactivate_url = reverse(
            "program_student_deactivate", args=[self.program.pk, self.student.pk]
        )
        self.assertContains(response, deactivate_url)
        self.assertNotContains(response, "Remove from Program")

    def test_detail_shows_mark_active_for_inactive_student(self):
        self.enrollment.active = False
        self.enrollment.save(update_fields=["active"])
        response = self.client.get(reverse("program_detail", args=[self.program.pk]))
        activate_url = reverse(
            "program_student_activate", args=[self.program.pk, self.student.pk]
        )
        self.assertContains(response, activate_url)

    def test_graduated_student_has_no_reactivate_button(self):
        graduated = Student.objects.create(
            preferred_first_name="Grad", last_name="Student", graduated=True
        )
        enrollment = Enrollment.objects.create(
            student=graduated, program=self.program, active=False
        )
        response = self.client.get(reverse("program_detail", args=[self.program.pk]))
        activate_url = reverse(
            "program_student_activate", args=[self.program.pk, graduated.pk]
        )
        self.assertNotContains(response, activate_url)
        self.assertTrue(Enrollment.objects.filter(pk=enrollment.pk).exists())


class ProgramStudentActiveTogglePermissionTests(TestCase):
    def setUp(self):
        self.program = Program.objects.create(name="FLL 2026")
        self.student = Student.objects.create(
            legal_first_name="Active", last_name="Student"
        )
        self.enrollment = Enrollment.objects.create(
            student=self.student, program=self.program, active=True
        )

    def test_anonymous_cannot_deactivate(self):
        url = reverse(
            "program_student_deactivate", args=[self.program.pk, self.student.pk]
        )
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.enrollment.refresh_from_db()
        self.assertTrue(self.enrollment.active)
