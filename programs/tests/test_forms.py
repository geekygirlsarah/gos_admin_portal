import datetime

from django import forms
from django.test import TestCase

from programs.forms import StudentForm
from programs.models import Adult


class StudentFormTests(TestCase):
    def setUp(self):
        self.parent1 = Adult.objects.create(
            first_name="Alex", last_name="Parent", is_parent=True
        )
        self.parent2 = Adult.objects.create(
            first_name="Sage", last_name="Guardian", is_parent=True
        )

    def test_primary_and_secondary_must_differ(self):
        form = StudentForm(
            data={
                "legal_first_name": "Taylor",
                "last_name": "Doe",
                "primary_contact": self.parent1.id,
                "secondary_contact": self.parent1.id,
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("secondary_contact", form.errors)

    def test_grade_selector_sets_graduation_year(self):
        # Grade 12 should set graduation_year to current end-of-year (June/July boundary logic)
        today = datetime.date.today()
        end_year = today.year + (1 if today.month >= 7 else 0)
        form = StudentForm(
            data={
                "legal_first_name": "Jamie",
                "last_name": "Lee",
                "grade_selector": "12",
                "date_of_birth": "2010-01-01",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        student = form.save()
        self.assertEqual(student.graduation_year, end_year)

    def test_parents_sync_includes_primary_and_secondary(self):
        form = StudentForm(
            data={
                "legal_first_name": "Robin",
                "last_name": "Quinn",
                "primary_contact": self.parent1.id,
                "secondary_contact": self.parent2.id,
                "parents": [self.parent1.id],  # only p1 preselected
                "date_of_birth": "2010-01-01",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        student = form.save()
        # adults should include both parents including secondary
        adult_ids = set(student.adults.values_list("id", flat=True))
        self.assertSetEqual(adult_ids, {self.parent1.id, self.parent2.id})

    def test_state_field_is_dropdown(self):
        form = StudentForm()
        self.assertIsInstance(form.fields["state"].widget, forms.Select)

    def test_state_field_default_is_PA(self):
        form = StudentForm()
        # StudentForm is a ModelForm, it should pick up the default from the model field
        # but let's see if it's in the initial attribute.
        # Actually ModelForm fields have `initial` attribute based on model's default.
        self.assertEqual(form.fields["state"].initial, "PA")
