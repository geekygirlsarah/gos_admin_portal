from django.test import Client, TestCase
from django.urls import reverse

from programs.models import Program, School

from .models import ApplicationOTP, StudentApplication


class ApplyWizardTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.program = Program.objects.create(
            name="Test Program",
            active=True,
            start_date="2026-01-01",
            end_date="2026-12-31",
        )
        self.school = School.objects.create(name="Test School")

    def test_apply_flow_student(self):
        # 1. Intro
        response = self.client.get(reverse("apply_start"), follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Start Application")
        response = self.client.post(reverse("apply_step", kwargs={"step": "intro"}))
        self.assertRedirects(
            response, reverse("apply_step", kwargs={"step": "program"})
        )

        # 2. Program Select
        response = self.client.post(
            reverse("apply_step", kwargs={"step": "program"}),
            {"program": self.program.id},
        )
        self.assertRedirects(response, reverse("apply_step", kwargs={"step": "role"}))

        # 3. Role Select (Student)
        response = self.client.post(
            reverse("apply_step", kwargs={"step": "role"}), {"role": "student"}
        )
        self.assertRedirects(
            response, reverse("apply_step", kwargs={"step": "student_info"})
        )

        # 4. Student Info (with OTP)
        student_data = {
            "preferred_first_name": "Jane",
            "last_name": "Doe",
            "date_of_birth": "2010-01-01",
            "address": "123 Main St",
            "city": "Pittsburgh",
            "state": "PA",
            "zip_code": "15213",
            "email": "jane@example.com",
            "school": self.school.id,
            "grade": 9,
            "tshirt_size": "Adult S",
        }
        # First POST sends OTP
        response = self.client.post(
            reverse("apply_step", kwargs={"step": "student_info"}), student_data
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["otp_sent"])

        otp = ApplicationOTP.objects.filter(email="jane@example.com").last()
        student_data["student_otp"] = otp.code

        # Second POST verifies and proceeds
        response = self.client.post(
            reverse("apply_step", kwargs={"step": "student_info"}), student_data
        )
        self.assertRedirects(
            response, reverse("apply_step", kwargs={"step": "student_essay"})
        )

        # 5. Student Essay
        essay_data = {
            "interest_reason": "I love robots.",
            "hope_to_gain": "Skills.",
        }
        response = self.client.post(
            reverse("apply_step", kwargs={"step": "student_essay"}), essay_data
        )
        self.assertRedirects(
            response, reverse("apply_step", kwargs={"step": "parent_verify"})
        )

        # 6. Parent Verify
        parent_verify_data = {"email": "parent@example.com"}
        response = self.client.post(
            reverse("apply_step", kwargs={"step": "parent_verify"}), parent_verify_data
        )
        self.assertEqual(response.status_code, 200)

        otp_p = ApplicationOTP.objects.filter(email="parent@example.com").last()
        parent_verify_data["otp"] = otp_p.code
        response = self.client.post(
            reverse("apply_step", kwargs={"step": "parent_verify"}), parent_verify_data
        )
        self.assertRedirects(
            response, reverse("apply_step", kwargs={"step": "parent1_info"})
        )

        # 7. Parent 1 Info
        parent1_data = {
            "parent1_preferred_first_name": "John",
            "parent1_last_name": "Doe",
            "parent1_phone_number": "555-1234",
            "parent1_email": "parent@example.com",
            "parent1_email_notices": True,
        }
        response = self.client.post(
            reverse("apply_step", kwargs={"step": "parent1_info"}), parent1_data
        )
        self.assertRedirects(
            response, reverse("apply_step", kwargs={"step": "parent2_info"})
        )

        # 8. Parent 2 Info
        response = self.client.post(
            reverse("apply_step", kwargs={"step": "parent2_info"}), {}
        )
        if response.status_code == 200:
            print(response.context["form"].errors)
        self.assertRedirects(
            response, reverse("apply_step", kwargs={"step": "confirm"})
        )

        # 9. Confirm
        response = self.client.post(reverse("apply_step", kwargs={"step": "confirm"}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Application Submitted!")

        self.assertEqual(StudentApplication.objects.count(), 1)
        app = StudentApplication.objects.first()
        self.assertEqual(app.preferred_first_name, "Jane")
        self.assertEqual(app.parent1_preferred_first_name, "John")
