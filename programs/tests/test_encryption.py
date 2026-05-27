from django.db import connection
from django.test import TestCase

from programs.models import Student


class EncryptionTest(TestCase):
    def test_student_fields_are_encrypted(self):
        # Create a student with sensitive data
        student = Student.objects.create(
            last_name="Test",
            allergies="Peanuts",
            medical_notes="Asthma",
            dietary_restrictions="Vegetarian",
        )

        # Verify that data is encrypted in the database
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT allergies, medical_notes, dietary_restrictions FROM programs_student WHERE id = %s",
                [student.id],
            )
            row = cursor.fetchone()
            # The data should NOT be "Peanuts", "Asthma", "Vegetarian"
            # Since Fernet encryption output starts with 'gAAAAAB', we can check that.
            self.assertFalse(row[0] == "Peanuts")
            self.assertFalse(row[1] == "Asthma")
            self.assertFalse(row[2] == "Vegetarian")

        # Verify that data can be read back as plain text
        s = Student.objects.get(id=student.id)
        self.assertEqual(s.allergies, "Peanuts")
        self.assertEqual(s.medical_notes, "Asthma")
        self.assertEqual(s.dietary_restrictions, "Vegetarian")
