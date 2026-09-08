from datetime import timedelta

from django.contrib.auth.models import Group, User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from attendance.models import AttendanceSession
from programs.models import Adult, Program, ProgramFeature, School, Student


class Phase3AttendanceUITests(TestCase):
    def setUp(self):
        self.lead_mentor_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.mentor_group, _ = Group.objects.get_or_create(name="Mentor")

        self.lead_user = User.objects.create_user(
            username="lead_attendance", password="password123"  # nosec B106
        )
        self.lead_user.groups.add(self.lead_mentor_group)

        self.mentor_user = User.objects.create_user(
            username="mentor_attendance", password="password123"  # nosec B106
        )
        self.mentor_user.groups.add(self.mentor_group)
        self.mentor_adult = Adult.objects.create(
            user=self.mentor_user, is_mentor=True, mentor_active=True
        )

        self.feat_att, _ = ProgramFeature.objects.get_or_create(
            key="attendance", defaults={"name": "Attendance"}
        )
        self.program = Program.objects.create(name="FTC 2026-2027", active=True)
        self.program.features.add(self.feat_att)

        self.school = School.objects.create(name="Central High")
        self.student = Student.objects.create(
            legal_first_name="Alex",
            preferred_first_name="Alex",
            last_name="Smith",
            school=self.school,
            graduation_year=2027,
        )

    def test_all_attendance_kpi_cards_and_export_dropdown(self):
        # Create closed and open sessions
        now = timezone.now()
        AttendanceSession.objects.create(
            program=self.program,
            student=self.student,
            check_in=now - timedelta(hours=3),
            check_out=now - timedelta(hours=1),
            duration_minutes=120,
        )
        AttendanceSession.objects.create(
            program=self.program,
            student=self.student,
            check_in=now - timedelta(minutes=45),
            check_out=None,
        )

        self.client.login(
            username="lead_attendance", password="password123"
        )  # nosec B106
        resp = self.client.get(reverse("all_attendance"))
        self.assertEqual(resp.status_code, 200)

        # Check KPI summary cards
        self.assertContains(resp, "Total Sessions")
        self.assertContains(resp, "Total Logged Hours")
        self.assertContains(resp, "Open Sessions")

        # Check Export dropdown preserving button IDs
        self.assertContains(resp, 'id="exportCsvBtn"')
        self.assertContains(resp, 'id="exportXlsxBtn"')

    def test_who_is_here_summary_metrics_and_stale_alert(self):
        now = timezone.localtime()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        # 1 today active session
        AttendanceSession.objects.create(
            program=self.program,
            student=self.student,
            check_in=today_start + timedelta(hours=2),
            check_out=None,
        )

        # 1 stale session from yesterday
        AttendanceSession.objects.create(
            program=self.program,
            visitor_name="Visiting Team 9999",
            check_in=today_start - timedelta(hours=20),
            check_out=None,
        )

        self.client.login(
            username="mentor_attendance", password="password123"
        )  # nosec B106
        resp = self.client.get(reverse("attendance_active"))
        self.assertEqual(resp.status_code, 200)

        # Metric cards
        self.assertContains(resp, "Currently Signed In")
        self.assertContains(resp, "Stale Sessions")

        # Stale resolution controls
        self.assertContains(resp, reverse("close_stale_attendance_sessions"))
        self.assertContains(resp, 'name="hours"')
        self.assertContains(resp, "Close All Stale")
