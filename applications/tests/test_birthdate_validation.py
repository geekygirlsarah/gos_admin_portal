import datetime

from django.contrib import messages
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from applications.models import Application
from programs.models import Program


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class BirthdateValidationTests(TestCase):
    def setUp(self):
        from programs.models import School

        School.objects.get_or_create(name="Pittsburgh High")
        self.app = Application.objects.create(
            applicant_type=Application.Type.STUDENT,
            email="student@example.com",
            current_step=5,
            email_verified_at=timezone.now(),
            status=Application.Status.EMAIL_VERIFIED,
        )

    def test_birthdate_required(self):
        response = self.client.post(
            reverse("apply_step5", kwargs={"app_id": self.app.application_id}),
            {
                "legal_first_name": "Grace",
                "last_name": "Hopper",
                "address": "123 Main St",
                "city": "Pittsburgh",
                "state": "PA",
                "zip_code": "15213",
                "tshirt_size": "M",
                "school_name": "Pittsburgh High",
                "grade": "9",
                # Missing date_of_birth
            },
            follow=True,
        )
        self.assertContains(response, "This field is required")

    def test_birthdate_future_date_invalid(self):
        future_date = timezone.localdate() + datetime.timedelta(days=1)
        response = self.client.post(
            reverse("apply_step5", kwargs={"app_id": self.app.application_id}),
            {
                "legal_first_name": "Grace",
                "last_name": "Hopper",
                "address": "123 Main St",
                "city": "Pittsburgh",
                "state": "PA",
                "zip_code": "15213",
                "tshirt_size": "M",
                "date_of_birth": future_date.strftime("%Y-%m-%d"),
                "school_name": "Pittsburgh High",
                "grade": "9",
            },
            follow=True,
        )
        self.assertContains(response, "Date of birth cannot be in the future")

    def test_birthdate_19_older_invalid(self):
        # 20 years old
        dob = timezone.localdate() - datetime.timedelta(days=20 * 365)
        response = self.client.post(
            reverse("apply_step5", kwargs={"app_id": self.app.application_id}),
            {
                "legal_first_name": "Grace",
                "last_name": "Hopper",
                "address": "123 Main St",
                "city": "Pittsburgh",
                "state": "PA",
                "zip_code": "15213",
                "tshirt_size": "M",
                "date_of_birth": dob.strftime("%Y-%m-%d"),
                "school_name": "Pittsburgh High",
                "grade": "9",
            },
            follow=True,
        )
        self.assertContains(response, "student is over 18")

    def test_birthdate_young_allowed_with_confirmation(self):
        # 4 years old (requires warning)
        dob = timezone.localdate() - datetime.timedelta(days=4 * 365)

        # 1. First attempt without confirmation -> should re-render with warning + confirm checkbox
        response = self.client.post(
            reverse("apply_step5", kwargs={"app_id": self.app.application_id}),
            {
                "legal_first_name": "Grace",
                "last_name": "Hopper",
                "address": "123 Main St",
                "city": "Pittsburgh",
                "state": "PA",
                "zip_code": "15213",
                "tshirt_size": "M",
                "date_of_birth": dob.strftime("%Y-%m-%d"),
                "school_name": "Pittsburgh High",
                "grade": "9",
            },
            follow=True,
        )
        self.assertContains(response, "seems a bit young")
        self.assertContains(response, "I confirm this birthdate is correct")

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
                "tshirt_size": "M",
                "date_of_birth": dob.strftime("%Y-%m-%d"),
                "confirm_age": "on",
                "school_name": "Pittsburgh High",
                "grade": "9",
            },
            follow=True,
        )
        self.assertRedirects(
            response, reverse("apply_step6", kwargs={"app_id": self.app.application_id})
        )
