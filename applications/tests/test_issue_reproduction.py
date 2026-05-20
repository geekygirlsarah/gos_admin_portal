from __future__ import annotations

from django.test import TestCase
from django.urls import reverse
from applications.models import Application
from programs.models import Program

class SubmittedNamesReproductionTest(TestCase):
    """Test displaying student and parent names on the application thanks page."""
    
    def setUp(self):
        self.program = Program.objects.create(
            name="Girls of Steel Program",
            year=2026,
            active=True,
        )

    def test_submitted_page_shows_student_and_parent_names(self):
        application = Application.objects.create(
            applicant_type=Application.Type.STUDENT,
            email="student@example.com",
            program=self.program,
            status=Application.Status.SUBMITTED,
            data={
                "step5": {
                    "legal_first_name": "Jane",
                    "last_name": "Doe",
                },
                "step6": {
                    "first_name": "John",
                    "last_name": "Doe",
                },
                "step7": {
                    "first_name": "Mary",
                    "last_name": "Doe",
                },
            }
        )
        url = reverse("apply_submitted", kwargs={"app_id": application.application_id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        
        self.assertContains(response, "Jane Doe") # Student
        self.assertContains(response, "John Doe") # Primary Parent
        self.assertContains(response, "Mary Doe") # Secondary Parent

    def test_submitted_page_shows_mentor_name(self):
        application = Application.objects.create(
            applicant_type=Application.Type.MENTOR,
            email="mentor@example.com",
            program=self.program,
            status=Application.Status.SUBMITTED,
            data={
                "mentor_info": {
                    "legal_first_name": "James",
                    "last_name": "Smith",
                }
            }
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
                "step5": {"legal_first_name": "Jane", "last_name": "Doe"},
                "step6": {"first_name": "John", "last_name": "Doe"},
                "step7": {"_skipped": True},
            }
        )
        url = reverse("apply_submitted", kwargs={"app_id": application.application_id})
        response = self.client.get(url)
        self.assertNotContains(response, "Secondary parent")


from django.core import mail
from django.test import override_settings
from applications.services import send_otp_email

@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class EmailSenderNameReproductionTest(TestCase):
    def test_default_from_email_with_name(self):
        app = Application.objects.create(email="test@example.com")
        with override_settings(DEFAULT_FROM_EMAIL="noreply@girlsofsteelrobotics.org", DEFAULT_FROM_NAME="Girls of Steel Admin"):
            send_otp_email(app, "123456")
            
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].from_email, '"Girls of Steel Admin" <noreply@girlsofsteelrobotics.org>')
