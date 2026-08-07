"""Tests for graduation year validation."""

from django.test import TestCase
from django.utils import timezone

from applications.forms import StudentInfoForm


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
            self.assertNotIn("graduation_year", form.errors)
