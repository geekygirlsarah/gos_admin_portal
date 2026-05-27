from datetime import date

from django.core.exceptions import ValidationError
from django.test import TestCase

from applications.forms import MentorInfoForm, ParentInfoForm, StudentInfoForm
from programs.forms import AdultForm, StudentForm
from programs.models import Adult, School, Student


class PhoneValidationTestCase(TestCase):
    def setUp(self):
        School.objects.get_or_create(name="Pittsburgh High")

    def test_student_model_phone_validation(self):
        date_of_birth_year = date.today().year - 12
        student = Student(
            legal_first_name="Test",
            last_name="Student",
            cell_phone_number="12345",  # Invalid: too short
            date_of_birth=date(date_of_birth_year, 1, 1),
        )
        with self.assertRaises(ValidationError):
            student.full_clean()

        student.cell_phone_number = "12345678901"  # Invalid: too long
        with self.assertRaises(ValidationError):
            student.full_clean()

        student.cell_phone_number = "1234567890"  # Valid: 10 digits
        student.full_clean()  # Should not raise

        student.cell_phone_number = "(123) 456-7890"  # Valid: 10 digits after stripping
        student.full_clean()  # Should not raise

    def test_adult_model_phone_validation(self):
        adult = Adult(
            first_name="Test",
            last_name="Adult",
            phone_number="1234567890",
            cell_phone="1234567890",
            home_phone="1234567890",
            emergency_contact_phone="1234567890",
        )
        adult.full_clean()  # Should pass

        # Testing multiple fields
        fields = ["phone_number", "cell_phone", "home_phone", "emergency_contact_phone"]
        for field in fields:
            original_val = getattr(adult, field)
            setattr(adult, field, "123")  # Invalid
            with self.assertRaises(ValidationError):
                adult.full_clean()
            setattr(adult, field, original_val)  # Restore valid value

    def test_student_form_validation(self):
        form_data = {
            "legal_first_name": "Test",
            "last_name": "Student",
            "cell_phone_number": "123",
            "date_of_birth": "2010-01-01",
        }
        form = StudentForm(data=form_data)
        self.assertFalse(form.is_valid())
        self.assertIn("cell_phone_number", form.errors)

        form_data["cell_phone_number"] = "1234567890"
        form = StudentForm(data=form_data)
        self.assertTrue(form.is_valid())

    def test_application_forms_validation(self):
        # StudentInfoForm
        form = StudentInfoForm(
            data={
                "legal_first_name": "A",
                "last_name": "B",
                "cell_phone_number": "123",
                "date_of_birth": "2010-01-01",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("cell_phone_number", form.errors)

        form = StudentInfoForm(
            data={
                "legal_first_name": "A",
                "last_name": "B",
                "cell_phone_number": "1234567890",
                "address": "123 Main St",
                "city": "Pittsburgh",
                "state": "PA",
                "zip_code": "15213",
                "tshirt_size": "M",
                "date_of_birth": "2010-01-01",
                "school_name": "Pittsburgh High",
                "grade": "9",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)

        # ParentInfoForm
        form = ParentInfoForm(
            data={
                "first_name": "A",
                "last_name": "B",
                "email": "a@b.com",
                "cell_phone": "123",
                "address": "123 Main St",
                "city": "Pittsburgh",
                "state": "PA",
                "zip_code": "15213",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("cell_phone", form.errors)

        form = ParentInfoForm(
            data={
                "first_name": "A",
                "last_name": "B",
                "email": "a@b.com",
                "cell_phone": "1234567890",
                "address": "123 Main St",
                "city": "Pittsburgh",
                "state": "PA",
                "zip_code": "15213",
                "relationship_to_student": "parent",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)

        # MentorInfoForm
        form = MentorInfoForm(
            data={"legal_first_name": "A", "last_name": "B", "cell_phone": "123"}
        )
        self.assertFalse(form.is_valid())
        self.assertIn("cell_phone", form.errors)

        form = MentorInfoForm(
            data={"legal_first_name": "A", "last_name": "B", "cell_phone": "1234567890"}
        )
        self.assertTrue(form.is_valid())
