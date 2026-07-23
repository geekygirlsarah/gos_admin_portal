from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from programs.models import Enrollment, Program, Student


class InactiveStudentTest(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            username="admin",
            password="password",
            email="admin@example.com",  # nosec B106
        )
        self.client.login(username="admin", password="password")  # nosec B106

        self.program = Program.objects.create(
            name="Test Program",
            active=True,
            start_date="2026-01-01",
            end_date="2026-12-31",
        )

        self.student = Student.objects.create(
            first_name="Active",
            last_name="Student",
            personal_email="active@example.com",
        )
        self.enrollment = Enrollment.objects.create(
            student=self.student, program=self.program, active=True
        )

        self.inactive_student = Student.objects.create(
            first_name="Inactive",
            last_name="Student",
            personal_email="inactive@example.com",
        )
        self.inactive_enrollment = Enrollment.objects.create(
            student=self.inactive_student, program=self.program, active=False
        )

    def test_program_detail_lists_inactive_enrollment_correctly(self):
        """
        Verify that Enrollment.active=False students are shown in the 'inactive' section
        of the program detail page, even if student.graduated is False.
        """
        response = self.client.get(reverse("program_detail", args=[self.program.pk]))
        self.assertEqual(response.status_code, 200)

        active_enrollments = response.context["active_enrollments"]
        inactive_enrollments = response.context["inactive_enrollments"]

        self.assertIn(self.enrollment, active_enrollments)
        self.assertNotIn(self.inactive_enrollment, active_enrollments)
        self.assertIn(self.inactive_enrollment, inactive_enrollments)

    def test_messaging_excludes_inactive_enrollments(self):
        """
        Verify that Enrollment.active=False students do NOT receive emails.
        """
        from django.core import mail

        url = reverse("program_email", args=[self.program.pk])
        data = {
            "program": self.program.pk,
            "recipient_groups": ["students"],
            "subject": "Test Subject",
            "body": "Test Body",
            "from_account": "DEFAULT",
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)  # Redirects on success

        # Check sent emails
        all_recipients = []
        for m in mail.outbox:
            all_recipients.extend(m.to)
            all_recipients.extend(m.bcc)

        self.assertIn(self.student.personal_email, all_recipients)
        self.assertNotIn(self.inactive_student.personal_email, all_recipients)

    def test_enrollment_update_view_handles_active_flag(self):
        """
        Verify that ProgramEnrollmentUpdateView can update the active flag.
        """
        url = reverse("program_enrollment_update", args=[self.program.pk])
        data = {"enrollment_id": self.enrollment.id, "active": "false"}
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)

        self.enrollment.refresh_from_db()
        self.assertFalse(self.enrollment.active)
