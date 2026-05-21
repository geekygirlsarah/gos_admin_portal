from django.test import TestCase
from django.core.exceptions import ValidationError
from programs.models import Student, Adult
from programs.forms import StudentForm, AdultForm
from applications.forms import StudentInfoForm, ParentInfoForm, MentorInfoForm

class PhoneValidationTestCase(TestCase):
    def test_student_model_phone_validation(self):
        student = Student(
            legal_first_name="Test",
            last_name="Student",
            cell_phone_number="12345" # Invalid: too short
        )
        with self.assertRaises(ValidationError):
            student.full_clean()

        student.cell_phone_number = "12345678901" # Invalid: too long
        with self.assertRaises(ValidationError):
            student.full_clean()

        student.cell_phone_number = "1234567890" # Valid: 10 digits
        student.full_clean() # Should not raise

        student.cell_phone_number = "(123) 456-7890" # Valid: 10 digits after stripping
        student.full_clean() # Should not raise

    def test_adult_model_phone_validation(self):
        adult = Adult(
            first_name="Test",
            last_name="Adult",
            phone_number="1234567890",
            cell_phone="1234567890",
            home_phone="1234567890",
            emergency_contact_phone="1234567890"
        )
        adult.full_clean() # Should pass

        # Testing multiple fields
        fields = ['phone_number', 'cell_phone', 'home_phone', 'emergency_contact_phone']
        for field in fields:
            original_val = getattr(adult, field)
            setattr(adult, field, "123") # Invalid
            with self.assertRaises(ValidationError):
                adult.full_clean()
            setattr(adult, field, original_val) # Restore valid value

    def test_student_form_validation(self):
        form_data = {
            'legal_first_name': 'Test',
            'last_name': 'Student',
            'cell_phone_number': '123'
        }
        form = StudentForm(data=form_data)
        self.assertFalse(form.is_valid())
        self.assertIn('cell_phone_number', form.errors)

        form_data['cell_phone_number'] = '1234567890'
        form = StudentForm(data=form_data)
        self.assertTrue(form.is_valid())

    def test_application_forms_validation(self):
        # StudentInfoForm
        form = StudentInfoForm(data={'legal_first_name': 'A', 'last_name': 'B', 'cell_phone_number': '123'})
        self.assertFalse(form.is_valid())
        self.assertIn('cell_phone_number', form.errors)

        form = StudentInfoForm(data={'legal_first_name': 'A', 'last_name': 'B', 'cell_phone_number': '1234567890'})
        self.assertTrue(form.is_valid())

        # ParentInfoForm
        form = ParentInfoForm(data={'first_name': 'A', 'last_name': 'B', 'email': 'a@b.com', 'cell_phone': '123'})
        self.assertFalse(form.is_valid())
        self.assertIn('cell_phone', form.errors)

        form = ParentInfoForm(data={'first_name': 'A', 'last_name': 'B', 'email': 'a@b.com', 'cell_phone': '1234567890'})
        self.assertTrue(form.is_valid())

        # MentorInfoForm
        form = MentorInfoForm(data={'legal_first_name': 'A', 'last_name': 'B', 'cell_phone': '123'})
        self.assertFalse(form.is_valid())
        self.assertIn('cell_phone', form.errors)

        form = MentorInfoForm(data={'legal_first_name': 'A', 'last_name': 'B', 'cell_phone': '1234567890'})
        self.assertTrue(form.is_valid())
