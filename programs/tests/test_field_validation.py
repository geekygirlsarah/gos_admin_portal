"""Field validation tests: phone and ZIP code validation on models and forms."""

from datetime import date

from django.core.exceptions import ValidationError
from django.test import TestCase

from applications.forms import MentorInfoForm, ParentInfoForm, StudentInfoForm
from programs.forms import StudentForm
from programs.models import Adult, School, Student


class PhoneValidationTestCase(TestCase):
    def setUp(self):
        School.objects.get_or_create(name="Pittsburgh High")

    def test_student_model_phone_validation(self):
        date_of_birth_year = date.today().year - 12
        student = Student(
            legal_first_name="Test",
            preferred_first_name="Test",
            last_name="Student",
            phone_number="12345",
            date_of_birth=date(date_of_birth_year, 1, 1),
        )
        with self.assertRaises(ValidationError):
            student.full_clean()

        student.phone_number = "123456789012"
        with self.assertRaises(ValidationError):
            student.full_clean()

        student.phone_number = "21255512345"
        with self.assertRaises(ValidationError):
            student.full_clean()

        student.phone_number = "1234567890"
        student.full_clean()

        student.phone_number = "(123) 456-7890"
        student.full_clean()

        student.phone_number = "1-412-555-1234"
        student.full_clean()

        student.phone_number = "+1 (412) 555-1234"
        student.full_clean()

        student.phone_number = ""
        student.full_clean()

        student.phone_number = None
        student.full_clean()

    def test_adult_model_phone_validation(self):
        adult = Adult(
            legal_first_name="Test",
            last_name="Adult",
            phone_number="1234567890",
            emergency_contact_phone="1234567890",
        )
        adult.full_clean()

        fields = ["phone_number", "emergency_contact_phone"]
        for field in fields:
            original_val = getattr(adult, field)
            setattr(adult, field, "123")
            with self.assertRaises(ValidationError):
                adult.full_clean()
            setattr(adult, field, original_val)

        adult.phone_number = "123456789012"
        with self.assertRaises(ValidationError):
            adult.full_clean()

        adult.phone_number = "21255512345"
        with self.assertRaises(ValidationError):
            adult.full_clean()

        adult.phone_number = "1-412-555-1234"
        adult.full_clean()

        adult.phone_number = "+1 (412) 555-1234"
        adult.full_clean()

        adult.phone_number = ""
        adult.full_clean()

        adult.phone_number = None
        adult.full_clean()

        adult.emergency_contact_phone = "123456789012"
        with self.assertRaises(ValidationError):
            adult.full_clean()

        adult.emergency_contact_phone = ""
        adult.full_clean()

        adult.emergency_contact_phone = None
        adult.full_clean()

    def test_student_form_validation(self):
        form_data = {
            "legal_first_name": "Test",
            "last_name": "Student",
            "phone_number": "123",
            "date_of_birth": "2010-01-01",
        }
        form = StudentForm(data=form_data)
        self.assertFalse(form.is_valid())
        self.assertIn("phone_number", form.errors)

        form_data["phone_number"] = "1234567890"
        form = StudentForm(data=form_data)
        self.assertTrue(form.is_valid())

    def test_application_forms_validation(self):
        form = StudentInfoForm(
            data={
                "legal_first_name": "A",
                "last_name": "B",
                "phone_number": "123",
                "date_of_birth": "2010-01-01",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("phone_number", form.errors)

        form = StudentInfoForm(
            data={
                "legal_first_name": "A",
                "last_name": "B",
                "phone_number": "1234567890",
                "phone_type": "cell",
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

        form = ParentInfoForm(
            data={
                "legal_first_name": "A",
                "last_name": "B",
                "email": "a@b.com",
                "phone_number": "123",
                "phone_type": "cell",
                "address": "123 Main St",
                "city": "Pittsburgh",
                "state": "PA",
                "zip_code": "15213",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("phone_number", form.errors)

        form = ParentInfoForm(
            data={
                "legal_first_name": "A",
                "last_name": "B",
                "email": "a@b.com",
                "phone_number": "1234567890",
                "phone_type": "cell",
                "address": "123 Main St",
                "city": "Pittsburgh",
                "state": "PA",
                "zip_code": "15213",
                "relationship_to_student": "parent",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)

        form = MentorInfoForm(
            data={
                "legal_first_name": "A",
                "last_name": "B",
                "phone_number": "123",
                "phone_type": "cell",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("phone_number", form.errors)

        form = MentorInfoForm(
            data={
                "legal_first_name": "A",
                "last_name": "B",
                "phone_number": "1234567890",
                "phone_type": "cell",
            }
        )
        self.assertTrue(form.is_valid())


class ZipValidationTestCase(TestCase):
    def test_student_model_zip_validation(self):
        student = Student(
            legal_first_name="Test",
            preferred_first_name="Test",
            last_name="Student",
            date_of_birth="2010-01-01",
        )
        for invalid_zip in ["123", "1234", "123456", "abcde"]:
            student.zip_code = invalid_zip
            with self.assertRaises(ValidationError):
                student.full_clean()

        student.zip_code = "12345"
        student.full_clean()

    def test_student_form_zip_validation(self):
        form_data = {
            "legal_first_name": "Test",
            "last_name": "Student",
            "date_of_birth": "2010-01-01",
            "zip_code": "123",
        }
        form = StudentForm(data=form_data)
        self.assertFalse(form.is_valid())
        self.assertIn("zip_code", form.errors)

        form_data["zip_code"] = "12345"
        form = StudentForm(data=form_data)
        self.assertTrue(form.is_valid(), form.errors)

    def test_application_forms_zip_validation(self):
        form = StudentInfoForm(
            data={
                "legal_first_name": "A",
                "last_name": "B",
                "date_of_birth": "2010-01-01",
                "zip_code": "123",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("zip_code", form.errors)

        form = StudentInfoForm(
            data={
                "legal_first_name": "A",
                "last_name": "B",
                "date_of_birth": "2010-01-01",
                "zip_code": "12345",
            }
        )
        self.assertNotIn("zip_code", form.errors)
