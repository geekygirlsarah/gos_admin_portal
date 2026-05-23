from __future__ import annotations

import datetime

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from applications.models import Application
from programs.models import Adult, Program, Student


def _verified(**kwargs):
    """Convenience: create an application that has cleared Steps 1-4."""
    defaults = dict(
        applicant_type=Application.Type.PARENT,
        email="parent@example.com",
        current_step=5,
        email_verified_at=timezone.now(),
        status=Application.Status.EMAIL_VERIFIED,
    )
    defaults.update(kwargs)
    return Application.objects.create(**defaults)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class Step6ExperienceTests(TestCase):
    def setUp(self):
        self.app = _verified(current_step=6)

    def test_step6_get_renders(self):
        response = self.client.get(
            reverse("apply_step6", kwargs={"app_id": self.app.application_id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Experience and interest")

    def test_step6_post_saves_and_advances(self):
        response = self.client.post(
            reverse("apply_step6", kwargs={"app_id": self.app.application_id}),
            {
                "interest_reason": "I love robots",
                "hoped_gains": "Knowledge",
                "prior_robotics_experience": "None",
                "referral_source": "Friend",
            },
        )
        self.assertRedirects(
            response,
            reverse("apply_step7", kwargs={"app_id": self.app.application_id}),
            fetch_redirect_response=False,
        )
        self.app.refresh_from_db()
        self.assertEqual(self.app.data["step6"]["interest_reason"], "I love robots")
        self.assertEqual(self.app.current_step, 7)


class RenumberingTests(TestCase):
    def test_step5_post_advances_to_step6(self):
        app = _verified()
        response = self.client.post(
            reverse("apply_step5", kwargs={"app_id": app.application_id}),
            {
                "legal_first_name": "Grace",
                "last_name": "Hopper",
                "personal_email": "grace@example.com",
                "address": "123 Main St",
                "city": "Pittsburgh",
                "state": "PA",
                "zip_code": "15213",
                "tshirt_size": "M",
            },
        )
        self.assertRedirects(
            response,
            reverse("apply_step6", kwargs={"app_id": app.application_id}),
            fetch_redirect_response=False,
        )

    def test_step_urls_and_views(self):
        app = _verified()
        # Step 7 (was 6)
        response = self.client.get(
            reverse("apply_step7", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Primary parent / guardian")

        # Step 8 (was 7)
        response = self.client.get(
            reverse("apply_step8", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Secondary parent / guardian")

        # Step 9 (was 8)
        response = self.client.get(
            reverse("apply_step9", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Review and submit")


class ConversionTests(TestCase):
    def test_experience_fields_saved_to_student(self):
        from applications.services import convert_application_to_student

        program = Program.objects.create(name="Test", year=2026, active=True)
        app = _verified(program=program, status=Application.Status.APPROVED)
        app.data = {
            "step5": {
                "legal_first_name": "Test",
                "last_name": "Student",
                "personal_email": "test@example.com",
            },
            "step6": {
                "interest_reason": "Reason",
                "hoped_gains": "Gains",
                "prior_robotics_experience": "Exp",
                "referral_source": "Source",
            },
            "step7": {
                "first_name": "Parent",
                "last_name": "One",
                "email": "p1@example.com",
            },
        }
        app.save()

        student = convert_application_to_student(app)
        self.assertEqual(student.interest_reason, "Reason")
        self.assertEqual(student.hoped_gains, "Gains")
        self.assertEqual(student.prior_robotics_experience, "Exp")
        self.assertEqual(student.referral_source, "Source")
