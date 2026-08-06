"""Tests for the application submitted page rendering."""

from django.test import TestCase
from django.urls import reverse

from applications.models import Application
from programs.models import Program


class SubmittedNamesReproductionTest(TestCase):
    """Test displaying student and parent names on the application thanks page."""

    def setUp(self):
        self.program = Program.objects.create(name="Girls of Steel Program", active=True)

    def test_submitted_page_shows_student_and_parent_names(self):
        application = Application.objects.create(
            applicant_type=Application.Type.STUDENT,
            email="student@example.com",
            program=self.program,
            status=Application.Status.SUBMITTED,
            data={
                "step5-student": {"legal_first_name": "Jane", "last_name": "Doe"},
                "step7-primaryparent": {"first_name": "John", "last_name": "Doe"},
                "step8-secondaryparent": {"first_name": "Mary", "last_name": "Doe"},
            },
        )
        url = reverse("apply_submitted", kwargs={"app_id": application.application_id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Jane Doe")
        self.assertContains(response, "John Doe")
        self.assertContains(response, "Mary Doe")

    def test_submitted_page_shows_mentor_name(self):
        application = Application.objects.create(
            applicant_type=Application.Type.MENTOR,
            email="mentor@example.com",
            program=self.program,
            status=Application.Status.SUBMITTED,
            data={
                "mentor_info": {"legal_first_name": "James", "last_name": "Smith"},
            },
        )
        url = reverse("apply_submitted", kwargs={"app_id": application.application_id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "James Smith")

    def test_submitted_page_hides_skipped_secondary_parent(self):
        application = Application.objects.create(
            applicant_type=Application.Type.STUDENT,
            email="student@example.com",
            program=self.program,
            status=Application.Status.SUBMITTED,
            data={
                "step5-student": {"legal_first_name": "Jane", "last_name": "Doe"},
                "step7-primaryparent": {"first_name": "John", "last_name": "Doe"},
                "step8-secondaryparent": {"_skipped": True},
            },
        )
        url = reverse("apply_submitted", kwargs={"app_id": application.application_id})
        response = self.client.get(url)
        self.assertNotContains(response, "Secondary parent")
