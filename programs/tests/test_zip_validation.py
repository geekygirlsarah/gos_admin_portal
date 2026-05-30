from django.core.exceptions import ValidationError
from django.test import TestCase

from applications.forms import StudentInfoForm
from programs.forms import StudentForm
from programs.models import Student


class ZipValidationTestCase(TestCase):
    def test_student_model_zip_validation(self):
        # We need a valid student for full_clean() to pass otherwise,
        # but let's just focus on zip_code field
        student = Student(
            legal_first_name="Test",
            last_name="Student",
            date_of_birth="2010-01-01",
        )

        # Test invalid ZIPs
        for invalid_zip in ["123", "1234", "123456", "abcde"]:
            student.zip_code = invalid_zip
            with self.assertRaises(ValidationError):
                student.full_clean()

        # Test valid ZIP
        student.zip_code = "12345"
        student.full_clean()  # Should not raise

    def test_student_form_zip_validation(self):
        form_data = {
            "legal_first_name": "Test",
            "last_name": "Student",
            "date_of_birth": "2010-01-01",
            "zip_code": "123",  # Invalid
        }
        form = StudentForm(data=form_data)
        self.assertFalse(form.is_valid())
        self.assertIn("zip_code", form.errors)

        form_data["zip_code"] = "12345"  # Valid
        form = StudentForm(data=form_data)
        self.assertTrue(form.is_valid(), form.errors)

    def test_application_forms_zip_validation(self):
        # StudentInfoForm
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
        # It might fail on other fields, but zip_code should be valid
        self.assertNotIn("zip_code", form.errors)
