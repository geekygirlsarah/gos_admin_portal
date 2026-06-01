import datetime

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from applications.models import Application
from programs.models import Program, School


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class GradeValidationTests(TestCase):
    def setUp(self):
        self.school, _ = School.objects.get_or_create(name="Pittsburgh High")
        self.program = Program.objects.create(
            name="Summer Camp",
            start_date=timezone.now().date() + datetime.timedelta(days=30),
            end_date=timezone.now().date() + datetime.timedelta(days=35),
            grade_range_start=4,
            grade_range_end=6,
            active=True,
        )
        self.app = Application.objects.create(
            applicant_type=Application.Type.PARENT,
            email="parent@example.com",
            program=self.program,
            current_step=5,
            email_verified_at=timezone.now(),
            status=Application.Status.EMAIL_VERIFIED,
        )

    def test_grade_within_range_no_warning(self):
        dob = timezone.localdate() - datetime.timedelta(days=10 * 365)
        response = self.client.post(
            reverse("apply_step5", kwargs={"app_id": self.app.application_id}),
            {
                "legal_first_name": "Grace",
                "last_name": "Hopper",
                "address": "123 Main St",
                "city": "Pittsburgh",
                "state": "PA",
                "zip_code": "15213",
                "date_of_birth": dob.strftime("%Y-%m-%d"),
                "school_name": self.school.name,
                "grade": "5",
            },
            follow=True,
        )
        self.assertNotContains(
            response, "seems to be outside the recommended grade range"
        )
        self.assertRedirects(
            response, reverse("apply_step6", kwargs={"app_id": self.app.application_id})
        )

    def test_grade_outside_range_requires_confirmation(self):
        dob = timezone.localdate() - datetime.timedelta(days=13 * 365)

        # 1. Attempt with grade 8 (range is 4-6) -> should show warning
        response = self.client.post(
            reverse("apply_step5", kwargs={"app_id": self.app.application_id}),
            {
                "legal_first_name": "Grace",
                "last_name": "Hopper",
                "address": "123 Main St",
                "city": "Pittsburgh",
                "state": "PA",
                "zip_code": "15213",
                "date_of_birth": dob.strftime("%Y-%m-%d"),
                "school_name": self.school.name,
                "grade": "8",
            },
            follow=True,
        )
        self.assertContains(response, "seems to be outside the recommended grade range")
        self.assertContains(response, "I confirm this grade is correct")

        # 2. Second attempt with confirmation -> should redirect to step 6
        response = self.client.post(
            reverse("apply_step5", kwargs={"app_id": self.app.application_id}),
            {
                "legal_first_name": "Grace",
                "last_name": "Hopper",
                "address": "123 Main St",
                "city": "Pittsburgh",
                "state": "PA",
                "zip_code": "15213",
                "date_of_birth": dob.strftime("%Y-%m-%d"),
                "school_name": self.school.name,
                "grade": "8",
                "confirm_grade": "on",
            },
            follow=True,
        )
        self.assertRedirects(
            response, reverse("apply_step6", kwargs={"app_id": self.app.application_id})
        )
