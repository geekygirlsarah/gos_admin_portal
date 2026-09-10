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

    def test_all_attendance_unique_attendees_breakdown(self):
        now = timezone.now()
        # 1 mentor session
        AttendanceSession.objects.create(
            program=self.program,
            adult=self.mentor_adult,
            check_in=now - timedelta(hours=3),
            check_out=now - timedelta(hours=2),
            duration_minutes=60,
        )
        # 2 sessions for the same student (1 unique student)
        for hours in (4, 2):
            AttendanceSession.objects.create(
                program=self.program,
                student=self.student,
                check_in=now - timedelta(hours=hours),
                check_out=now - timedelta(hours=hours - 1),
                duration_minutes=60,
            )
        # 2 visitor sessions: 2 distinct visitor names
        for name in ("Visit A", "Visit A", "Visit B"):
            AttendanceSession.objects.create(
                program=self.program,
                student=None,
                adult=None,
                visitor_name=name,
                check_in=now - timedelta(hours=1),
                check_out=now - timedelta(minutes=30),
                duration_minutes=30,
            )

        self.client.login(
            username="lead_attendance", password="password123"  # nosec B106
        )
        resp = self.client.get(reverse("all_attendance"))
        self.assertEqual(resp.status_code, 200)

        self.assertEqual(resp.context["unique_attendees"], 4)
        self.assertEqual(resp.context["unique_students"], 1)
        self.assertEqual(resp.context["unique_mentors"], 1)
        self.assertEqual(resp.context["unique_visitors"], 2)

        content = resp.content.decode()
        self.assertIn("Unique Attendees", content)
        self.assertIn("1 student", content)
        self.assertIn("1 mentor", content)
        self.assertIn("2 visitors", content)

    def test_all_attendance_unique_attendees_breakdown_full_range(self):
        """When no sessions exist, the breakdown reads 0 / 0 / 0."""
        self.client.login(
            username="lead_attendance", password="password123"  # nosec B106
        )
        resp = self.client.get(reverse("all_attendance"))
        self.assertEqual(resp.context["unique_attendees"], 0)
        self.assertEqual(resp.context["unique_students"], 0)
        self.assertEqual(resp.context["unique_mentors"], 0)
        self.assertEqual(resp.context["unique_visitors"], 0)

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
        self.assertContains(resp, "Unique Attendees")
        self.assertContains(resp, "Open Sessions")
        self.assertNotContains(resp, "Total Logged Hours")

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

        # CSP check
        self.assertIn("csp_nonce", resp.context)
        self.assertContains(resp, f'nonce="{resp.context["csp_nonce"]}"')
        self.assertContains(resp, 'id="whoIsHereProgramSelect"')
        self.assertNotContains(resp, "onchange=")
