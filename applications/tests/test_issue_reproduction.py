
from __future__ import annotations
import datetime
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from applications.models import Application
from programs.models import Program

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
class TestGradeRepopulation(TestCase):
    def setUp(self):
        self.program = Program.objects.create(
            name="Spring 2030",
            year=2030,
            start_date=timezone.localdate() + datetime.timedelta(days=60),
            end_date=timezone.localdate() + datetime.timedelta(days=120),
            active=True,
        )

    def test_step5_grade_repopulates_when_navigating_back(self):
        app = _verified(program=self.program)
        
        # 1. Post valid student info to step 5, including a grade
        self.client.post(
            reverse("apply_step5", kwargs={"app_id": app.application_id}),
            {
                "legal_first_name": "Grace",
                "first_name": "",
                "last_name": "Hopper",
                "pronouns": "",
                "personal_email": "grace@example.com",
                "address": "123 Main St",
                "city": "Pittsburgh",
                "state": "PA",
                "zip_code": "15213",
                "cell_phone_number": "",
                "school_name": "",
                "graduation_year": "",
                "tshirt_size": "M",
                "allergies": "",
                "dietary_restrictions": "",
                "medical_notes": "",
                "date_of_birth": "2010-01-01",
                "grade": "8",  # Added grade
            },
        )
        
        # Verify it advanced to step 6
        app.refresh_from_db()
        self.assertEqual(app.current_step, 6)
        
        # 2. Go back to step 5
        response = self.client.get(
            reverse("apply_step5", kwargs={"app_id": app.application_id})
        )
        
    def test_step5_grade_appears_in_review(self):
        app = _verified(program=self.program, current_step=9)
        # Add grade to step 5 data
        data = app.data or {}
        data["step5"] = {"grade": "8"}
        app.data = data
        app.save()

        # Get review page
        response = self.client.get(
            reverse("apply_step9", kwargs={"app_id": app.application_id})
        )
        
        # Check if grade is in the response
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Grade")
        self.assertContains(response, "8")

    def test_step9_grade_calculated_from_graduation_year(self):
        from programs.utils import get_academic_year_ending
        academic_year_ending = get_academic_year_ending()
        
        # Grade 8
        # grad_year = academic_year_ending + (12 - 8) = academic_year_ending + 4
        grad_year = academic_year_ending + 4
        
        app = _verified(program=self.program, current_step=9)
        # Add grad_year to step 5 data, NO grade
        data = app.data or {}
        data["step5"] = {"graduation_year": grad_year}
        app.data = data
        app.save()

        # Get review page
        response = self.client.get(
            reverse("apply_step9", kwargs={"app_id": app.application_id})
        )
        
        # Check if grade is in the response (it should be 8 now)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Grade")
        self.assertContains(response, "8")
