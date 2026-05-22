from __future__ import annotations

import datetime

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from applications.forms import StudentInfoForm
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
            },
        )
        url = reverse("apply_submitted", kwargs={"app_id": application.application_id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        self.assertContains(response, "Jane Doe")  # Student
        self.assertContains(response, "John Doe")  # Primary Parent
        self.assertContains(response, "Mary Doe")  # Secondary Parent

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
                "step5": {"legal_first_name": "Jane", "last_name": "Doe"},
                "step6": {"first_name": "John", "last_name": "Doe"},
                "step7": {"_skipped": True},
            },
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
        with override_settings(
            DEFAULT_FROM_EMAIL="noreply@girlsofsteelrobotics.org",
            DEFAULT_FROM_NAME="Girls of Steel Admin",
        ):
            send_otp_email(app, "123456")

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(
            mail.outbox[0].from_email,
            '"Girls of Steel Admin" <noreply@girlsofsteelrobotics.org>',
        )


class GraduationYearValidationReproductionTest(TestCase):
    def test_graduation_year_cannot_be_in_past(self):
        current_year = timezone.now().year
        past_year = current_year - 1

        data = {
            "legal_first_name": "Jane",
            "last_name": "Doe",
            "graduation_year": past_year,
        }
        form = StudentInfoForm(data=data)
        self.assertIn("graduation_year", form.errors)
        self.assertEqual(
            form.errors["graduation_year"],
            [f"Ensure this value is greater than or equal to {current_year}."],
        )

    def test_graduation_year_can_be_current_or_future(self):
        current_year = timezone.now().year
        for year in [current_year, current_year + 1]:
            data = {
                "legal_first_name": "Jane",
                "last_name": "Doe",
                "graduation_year": year,
            }
            form = StudentInfoForm(data=data)
            # We check for graduation_year errors specifically
            self.assertNotIn("graduation_year", form.errors)


class WizardBackNavigationReproductionTests(TestCase):
    def setUp(self):
        self.program = Program.objects.create(
            name="Spring 2030",
            year=2030,
            start_date=timezone.localdate() + datetime.timedelta(days=60),
            active=True,
        )

    def test_back_button_from_step3_leads_to_step2_if_verified(self):
        # 1. Create an application that has verified email
        app = Application.objects.create(
            email="test@example.com",
            applicant_type="student",
            email_verified_at=timezone.now(),
            current_step=5,  # Beyond step 4
        )

        # 2. Check Step 3 "Back" button (Program selection)
        response = self.client.get(
            reverse("apply_step3", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(response.status_code, 200)
        # It should link to Step 2 now, because email is already verified
        self.assertContains(
            response, reverse("apply_step2", kwargs={"app_id": app.application_id})
        )
        self.assertNotContains(
            response, reverse("apply_step4", kwargs={"app_id": app.application_id})
        )

    def test_accessing_step4_while_verified_redirects_forward(self):
        app = Application.objects.create(
            email="test@example.com",
            applicant_type="student",
            email_verified_at=timezone.now(),
            current_step=5,
        )
        # 3. Access Step 4 while verified
        response = self.client.get(
            reverse("apply_step4", kwargs={"app_id": app.application_id})
        )
        # It should redirect to Step 3
        self.assertRedirects(
            response, reverse("apply_step3", kwargs={"app_id": app.application_id})
        )

    def test_back_button_from_step5_leads_to_step3(self):
        # 1. Create an application that has verified email and chosen a program
        app = Application.objects.create(
            email="test@example.com",
            applicant_type="student",
            email_verified_at=timezone.now(),
            program=self.program,
            current_step=5,
        )

        # 2. Check Step 5 "Back" button (Student info)
        response = self.client.get(
            reverse("apply_step5", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(response.status_code, 200)
        # It should link to Step 3
        self.assertContains(
            response, reverse("apply_step3", kwargs={"app_id": app.application_id})
        )
        self.assertNotContains(
            response, reverse("apply_step4", kwargs={"app_id": app.application_id})
        )

    def test_back_button_from_mentor_info_leads_to_step2_if_verified(self):
        # 1. Create a mentor application that has verified email
        app = Application.objects.create(
            email="mentor@example.com",
            applicant_type="mentor",
            email_verified_at=timezone.now(),
            current_step=5,
        )

        # 2. Check Mentor Info "Back" button
        response = self.client.get(
            reverse("apply_mentor_info", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(response.status_code, 200)
        # It should link to Step 2 now, because email is already verified
        self.assertContains(
            response, reverse("apply_step2", kwargs={"app_id": app.application_id})
        )
        self.assertNotContains(
            response, reverse("apply_step4", kwargs={"app_id": app.application_id})
        )
