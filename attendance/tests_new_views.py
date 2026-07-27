from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from programs.models import Program, Student
from attendance.models import AttendanceSession, KioskConfig
from django.utils import timezone

class AttendanceNewViewsTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username='mentor', password='password')
        self.user.is_staff = True
        self.user.is_superuser = True
        self.user.save()
        
        self.program = Program.objects.create(name="Test Program")
        from programs.models import ProgramFeature
        feature, _ = ProgramFeature.objects.get_or_create(key="attendance", defaults={"name": "Attendance"})
        self.program.features.add(feature)
        
        self.student = Student.objects.create(first_name="John", last_name="Doe", graduation_year=2026)
        
        self.session = AttendanceSession.objects.create(
            program=self.program,
            student=self.student,
            check_in=timezone.now()
        )
        
        self.client.login(username='mentor', password='password')

    def test_active_manifest_view(self):
        response = self.client.get(reverse('attendance_manifest'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "John Doe")
        self.assertContains(response, "Test Program")

    def test_active_manifest_filter(self):
        other_program = Program.objects.create(name="Other Program")
        response = self.client.get(reverse('attendance_manifest') + f'?program_id={other_program.id}')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "John Doe")

    def test_attendance_summary_view(self):
        # Close the session to record some duration
        self.session.check_out = self.session.check_in + timezone.timedelta(hours=2)
        self.session.recompute_duration()
        self.session.save()
        
        response = self.client.get(reverse('attendance_summary'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "John Doe")
        self.assertContains(response, "2h 0m")
