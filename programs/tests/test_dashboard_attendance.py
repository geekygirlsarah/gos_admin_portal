import datetime

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from attendance.models import AttendanceSession
from programs.models import Adult, Enrollment, Program, ProgramFeature, Student


class DashboardAttendanceTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="testuser", password="password"  # nosec B106
        )
        self.student = Student.objects.create(
            first_name="Test", last_name="Student", user=self.user
        )
        self.program = Program.objects.create(
            name="Test Program",
            active=True,
            start_date=timezone.now().date() - datetime.timedelta(days=30),
        )
        self.enrollment = Enrollment.objects.create(
            student=self.student, program=self.program
        )

        self.attendance_feature, _ = ProgramFeature.objects.get_or_create(
            key="attendance", defaults={"name": "Attendance"}
        )
        self.outreach_feature, _ = ProgramFeature.objects.get_or_create(
            key="outreach", defaults={"name": "Outreach"}
        )

        self.client.login(username="testuser", password="password")  # nosec B106

    def test_dashboard_no_features(self):
        """If neither feature is enabled, show 'No ... hours are tracked'."""
        response = self.client.get(reverse("profile_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No attendance hours are tracked")
        self.assertContains(response, "No outreach hours are tracked")
        self.assertNotContains(response, "Hours logged this week")

    def test_dashboard_with_attendance_feature(self):
        """If attendance feature is enabled, show hours."""
        self.program.features.add(self.attendance_feature)

        # Create some attendance sessions
        now = timezone.now()
        # Session from this week
        AttendanceSession.objects.create(
            program=self.program,
            student=self.student,
            check_in=now - datetime.timedelta(hours=2),
            check_out=now - datetime.timedelta(hours=1),
            duration_minutes=60,
        )
        # Session from 2 weeks ago
        AttendanceSession.objects.create(
            program=self.program,
            student=self.student,
            check_in=now - datetime.timedelta(days=14, hours=2),
            check_out=now - datetime.timedelta(days=14, hours=1),
            duration_minutes=60,
        )

        response = self.client.get(reverse("profile_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, "<strong>1.0</strong> hours logged this week", html=True
        )
        self.assertContains(
            response,
            "<strong>2.0</strong> hours logged since program started",
            html=True,
        )
        self.assertContains(response, "No outreach hours are tracked")

    def test_dashboard_parent_view(self):
        """Parent dashboard should also show student attendance."""
        parent_user = User.objects.create_user(
            username="parentuser", password="password"  # nosec B106
        )
        parent_adult = Adult.objects.create(
            user=parent_user, is_parent=True, first_name="Parent"
        )
        parent_adult.students.add(self.student)

        self.program.features.add(self.attendance_feature)

        # Create an attendance session
        now = timezone.now()
        AttendanceSession.objects.create(
            program=self.program,
            student=self.student,
            check_in=now - datetime.timedelta(hours=2),
            check_out=now - datetime.timedelta(hours=1),
            duration_minutes=60,
        )

        self.client.login(username="parentuser", password="password")  # nosec B106
        response = self.client.get(reverse("profile_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, "<strong>1.0</strong> hours logged this week", html=True
        )
        self.assertContains(response, "No outreach hours are tracked")

    def test_dashboard_with_outreach_feature(self):
        """If outreach feature is enabled, 'No outreach hours are tracked' should disappear."""
        self.program.features.add(self.outreach_feature)

        response = self.client.get(reverse("profile_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "No outreach hours are tracked")
        # Since we don't distinguish outreach hours yet, it shows the same stats card
        self.assertContains(
            response, "<strong>0.0</strong> hours logged this week", html=True
        )
