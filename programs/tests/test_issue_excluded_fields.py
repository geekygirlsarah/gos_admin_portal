import datetime

from django.core import mail
from django.test import Client, TestCase
from django.urls import reverse

from programs.models import Adult, Enrollment, Program, Student, StudentApplication


class ExcludedFieldsTests(TestCase):
    def setUp(self):
        self.program = Program.objects.create(
            name="Excluded Fields Program",
            year=2025,
            active=True,
            start_date="2025-09-01",
        )
        self.client = Client()

    def test_student_details_form_fields_excluded(self):
        # Parent flow: login/otp
        session = self.client.session
        session["apply_program_id"] = self.program.id
        session["apply_role"] = "parent"
        session["apply_parent_email"] = "parent@example.com"
        session.save()

        response = self.client.get(
            reverse("apply_wizard") + "?step=4_parent_student_details"
        )
        self.assertEqual(response.status_code, 200)

        # Verify sensitive fields are NOT in the form at all (not even as hidden)
        excluded = [
            "andrew_id",
            "andrew_email",
            "parent_name",
            "parent_email",
            "parent_phone",
        ]
        content = response.content.decode()
        for field in excluded:
            # Check for name="field" or id="id_field"
            self.assertNotIn(f'name="{field}"', content)
            self.assertNotIn(f'id="id_{field}"', content)

    def test_student_info_form_fields_excluded(self):
        # Student flow
        session = self.client.session
        session["apply_program_id"] = self.program.id
        session["apply_role"] = "student"
        session.save()

        response = self.client.get(reverse("apply_wizard") + "?step=3_student_info")
        self.assertEqual(response.status_code, 200)

        excluded = [
            "andrew_id",
            "andrew_email",
            "parent_name",
            "parent_email",
            "parent_phone",
        ]
        content = response.content.decode()
        for field in excluded:
            self.assertNotIn(f'name="{field}"', content)
            self.assertNotIn(f'id="id_{field}"', content)

    def test_student_flow_submission_with_separated_identity(self):
        session = self.client.session
        session["apply_program_id"] = self.program.id
        session["apply_role"] = "student"
        session.save()

        # Step 3_student_identity
        self.client.post(
            reverse("apply_wizard"),
            {
                "step": "3_student_identity",
                "legal_first_name": "Young",
                "last_name": "Student",
                "parent_email": "parent_of_young@example.com",
            },
            follow=True,
        )

        # Step 3_student_info
        response = self.client.post(
            reverse("apply_wizard"),
            {
                "step": "3_student_info",
                "grade_selector": "5",
            },
            follow=True,
        )

        self.assertContains(response, "Application Started")

        app = StudentApplication.objects.get(
            parent_email="parent_of_young@example.com", program=self.program
        )
        self.assertEqual(app.legal_first_name, "Young")
        self.assertEqual(app.last_name, "Student")
        self.assertEqual(app.status, "pending_parent")

    def test_parent_flow_submission_auto_injects_parent_info(self):
        # Create parent adult
        parent_adult = Adult.objects.create(
            first_name="Jane",
            last_name="Doe",
            personal_email="jane@example.com",
            cell_phone="555-0101",
            is_parent=True,
        )

        session = self.client.session
        session["apply_program_id"] = self.program.id
        session["apply_role"] = "parent"
        session["apply_parent_email"] = "jane@example.com"
        session["apply_student_id"] = "new"
        session.save()

        # Step 4_parent_student_identity
        self.client.post(
            reverse("apply_wizard"),
            {
                "step": "4_parent_student_identity",
                "legal_first_name": "Junior",
                "last_name": "Doe",
            },
            follow=True,
        )

        # Step 4_parent_student_details
        response = self.client.post(
            reverse("apply_wizard"),
            {
                "step": "4_parent_student_details",
                "grade_selector": "9",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "submitted successfully")

        app = StudentApplication.objects.get(
            legal_first_name="Junior", program=self.program
        )
        self.assertEqual(app.parent_email, "jane@example.com")
        self.assertEqual(app.parent_name, "Jane Doe")
        self.assertEqual(app.parent_phone, "555-0101")
        self.assertEqual(app.status, "pending")
