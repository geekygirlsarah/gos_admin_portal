from django.test import TestCase
from django.contrib.auth.models import User
from programs.models import Adult, Student
from programs.utils import convert_student_to_alumni

class AlumniConversionImprovedTests(TestCase):
    def test_conversion_links_student_and_transfers_user(self):
        # Setup student with a user
        user = User.objects.create_user(username='graduating_student', email='student@example.com')
        student = Student.objects.create(
            legal_first_name='Grad',
            last_name='Student',
            personal_email='student@example.com',
            user=user,
            address='123 Grad St',
            pronouns='they/them'
        )
        self.assertFalse(student.graduated)
        self.assertEqual(user.student_profile, student)

        # Convert
        adult, created, marked = convert_student_to_alumni(student)

        self.assertTrue(created)
        self.assertTrue(marked)
        self.assertTrue(adult.is_alumni)
        self.assertEqual(adult.student_record, student)
        
        # Verify user transfer
        student.refresh_from_db()
        adult.refresh_from_db()
        self.assertIsNone(student.user)
        self.assertEqual(adult.user, user)
        self.assertEqual(user.adult_profile, adult)

        # Verify status changes
        self.assertTrue(student.graduated)

        # Verify field copying
        self.assertEqual(adult.address, '123 Grad St')
        self.assertEqual(adult.pronouns, 'they/them')
        self.assertEqual(adult.personal_email, 'student@example.com')
        self.assertEqual(adult.alumni_email, 'student@example.com')

    def test_conversion_updates_existing_adult_and_links(self):
        # Setup existing adult (maybe a mentor who was also a student?)
        existing_adult = Adult.objects.create(
            first_name='Grad',
            last_name='Student',
            email='student@example.com',
            is_alumni=False
        )
        student = Student.objects.create(
            legal_first_name='Grad',
            last_name='Student',
            personal_email='student@example.com'
        )

        # Convert
        adult, created, marked = convert_student_to_alumni(student)

        self.assertFalse(created)
        self.assertEqual(adult, existing_adult)
        self.assertTrue(adult.is_alumni)
        self.assertEqual(adult.student_record, student)
        self.assertEqual(adult.alumni_email, 'student@example.com')
