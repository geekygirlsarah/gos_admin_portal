from datetime import date

from django.core.management import call_command
from django.test import TestCase

from programs.models import Adult, Enrollment, Program, ProgramFeature, School, Student


class SeedDbCommandTest(TestCase):
    def test_seed_db_command(self):
        """Test that the seed_db command creates the expected objects."""
        call_command("seed_db")

        # Check Programs
        self.assertEqual(Program.objects.count(), 6)
        this_year = date.today().year
        self.assertTrue(Program.objects.filter(year=this_year - 1).exists())
        self.assertTrue(Program.objects.filter(year=this_year).exists())
        self.assertTrue(Program.objects.filter(year=this_year + 1).exists())

        # Check Schools
        self.assertEqual(School.objects.count(), 2)

        # Check Adults
        # 4 parents + 1 lead mentor + 1 passing mentor + 1 expiring mentor + 2 alumni = 9
        self.assertEqual(Adult.objects.count(), 9)
        self.assertEqual(Adult.objects.filter(is_parent=True).count(), 4)
        self.assertEqual(Adult.objects.filter(is_mentor=True).count(), 3)
        self.assertEqual(Adult.objects.filter(is_alumni=True).count(), 2)

        # Check Students
        # 3 regular + 2 alumni students = 5
        self.assertEqual(Student.objects.count(), 5)

        # Check Enrollments
        # Student 1: 6 programs
        # Student 2: 6 programs
        # Student 3: 1 program
        # Alumni 1: 1 program
        # Alumni 2: 1 program
        # Total: 15
        self.assertEqual(Enrollment.objects.count(), 15)

        # Check email format
        student1 = Student.objects.get(personal_email="swithee+student1@andrew.cmu.edu")
        self.assertIn("+student1", student1.personal_email)

        # Check mentors
        passing_mentor = Adult.objects.get(first_name="Passing")
        self.assertTrue(passing_mentor.has_paca_clearance)
        self.assertTrue(passing_mentor.has_patch_clearance)
        self.assertTrue(passing_mentor.has_fbi_clearance)

        expiring_mentor = Adult.objects.get(first_name="Expiring")
        self.assertTrue(expiring_mentor.pa_clearances_expiration_date > date.today())
