from django.test import TestCase
from programs.models import Enrollment, Program, Student, SubTeam


class SubTeamTests(TestCase):
    def setUp(self):
        self.program = Program.objects.create(name="Test Program", year=2025)
        self.subteam = SubTeam.objects.create(
            name="Test SubTeam", program=self.program, color="#ff0000"
        )
        self.student = Student.objects.create(first_name="Test", last_name="Student")
        self.enrollment = Enrollment.objects.create(
            student=self.student, program=self.program
        )

    def test_subteam_creation(self):
        self.assertEqual(str(self.subteam), "Test SubTeam (Test Program)")

    def test_enrollment_subteam_assignment(self):
        self.enrollment.subteam = self.subteam
        self.enrollment.save()
        self.assertEqual(self.enrollment.subteam, self.subteam)
