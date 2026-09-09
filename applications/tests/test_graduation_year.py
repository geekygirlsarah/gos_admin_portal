"""Tests for graduation year validation."""

from datetime import date

from django.test import TestCase
from django.utils import timezone

from applications.forms import NOT_LISTED_SCHOOL, StudentInfoForm


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


class StudentInfoFormLabelTests(TestCase):
    def test_grade_label_references_july_1_of_school_year(self):
        # A program starting in the middle of the 2026-27 school year should
        # reference the July 1, 2026 rollover date, not the program start date.
        form = StudentInfoForm(program_start_date=date(2027, 1, 15))
        self.assertEqual(
            form.fields["grade"].label,
            "Grade going into the program as of July 1, 2026",
        )

    def test_grade_label_july_1_for_fall_start(self):
        # A program starting after July 1 belongs to the school year that is
        # beginning that summer (July 1 of the same calendar year).
        form = StudentInfoForm(program_start_date=date(2026, 9, 1))
        self.assertEqual(
            form.fields["grade"].label,
            "Grade going into the program as of July 1, 2026",
        )

    def test_school_name_offers_not_listed_option(self):
        form = StudentInfoForm()
        choice_values = [value for value, _ in form.fields["school_name"].choices]
        self.assertEqual(choice_values[-1], NOT_LISTED_SCHOOL)
