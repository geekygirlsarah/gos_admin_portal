from django.core import mail
from django.test import Client, TestCase
from django.urls import reverse

from programs.models import Adult, Enrollment, Program, Student, StudentApplication


class ApplicationWizardTests(TestCase):
    def setUp(self):
        self.program = Program.objects.create(
            name="Wizard Program", year=2025, active=True, start_date="2025-09-01"
        )
        self.client = Client()

    def test_wizard_start(self):
        response = self.client.get(reverse("apply_wizard"))
        # If it redirects, follow it
        if response.status_code == 302:
            response = self.client.get(response.url, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Select a Program")

    def test_step_1_program_selection(self):
        response = self.client.post(
            reverse("apply_wizard"), {"step": "1", "program": self.program.id}
        )
        # It might be 200 because render_step is called directly, or 302 if redirect is used.
        if response.status_code == 302:
            response = self.client.get(response.url)
        self.assertContains(response, "Who are you?")

    def test_step_2_role_selection_parent(self):
        # Set initial session data
        session = self.client.session
        session["apply_program_id"] = self.program.id
        session.save()

        response = self.client.post(
            reverse("apply_wizard"), {"step": "2", "role": "parent"}, follow=True
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Email Address")

    def test_parent_flow_otp_sent(self):
        session = self.client.session
        session["apply_program_id"] = self.program.id
        session["apply_role"] = "parent"
        session.save()

        response = self.client.post(
            reverse("apply_wizard"),
            {"step": "3_parent_identity", "email": "parent@example.com"},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Verification Code", mail.outbox[0].subject)
        self.assertContains(response, "Verification Code")

    def test_student_flow_handoff(self):
        session = self.client.session
        session["apply_program_id"] = self.program.id
        session["apply_role"] = "student"
        session.save()

        # Step 3_student_identity
        self.client.post(
            reverse("apply_wizard"),
            {
                "step": "3_student_identity",
                "legal_first_name": "Student",
                "last_name": "Test",
                "parent_email": "parent_handoff@example.com",
            },
            follow=True,
        )

        # Step 3 for student
        response = self.client.post(
            reverse("apply_wizard"),
            {
                "step": "3_student_info",
                "grade_selector": "10",
            },
            follow=True,
        )
        # print(response.content.decode())
        if response.status_code == 200:
            print("DEBUG CONTENT:")
            print(response.content.decode())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Application Started")

        app = StudentApplication.objects.get(
            parent_email="parent_handoff@example.com", program=self.program
        )
        self.assertEqual(app.status, "pending_parent")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(
            "complete your student's application",
            mail.outbox[0].subject.lower() if mail.outbox[0].subject else "",
        )
