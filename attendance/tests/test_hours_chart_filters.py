from datetime import datetime, timedelta

from django.contrib.auth.models import Group, User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from attendance.models import AttendanceSession
from programs.models import Program, ProgramFeature, Student


class AttendanceHoursChartFilterTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_superuser(
            username="admin", password="password"
        )  # nosec B106
        self.client.login(username="admin", password="password")  # nosec B106

        # Create a program with attendance feature
        self.program = Program.objects.create(
            name="Test Program",
            start_date=timezone.now().date() - timedelta(days=30),
            end_date=timezone.now().date() + timedelta(days=30),
            active=True,
        )
        feature, _ = ProgramFeature.objects.get_or_create(
            key="attendance", defaults={"name": "Attendance"}
        )
        self.program.features.add(feature)

        self.student = Student.objects.create(
            legal_first_name="John", last_name="Doe", graduated=False
        )

        # Create sessions on different days
        # Sunday=1, Monday=2, Tuesday=3, Wednesday=4, Thursday=5, Friday=6, Saturday=7 in Django check_in__week_day
        # In Python datetime.weekday(): Monday=0, ..., Sunday=6

        # Monday (2026-08-31)
        monday_dt = datetime(2026, 8, 31, 10, 0, tzinfo=timezone.get_current_timezone())
        AttendanceSession.objects.create(
            program=self.program,
            student=self.student,
            check_in=monday_dt,
            check_out=monday_dt + timedelta(hours=2),
            duration_minutes=120,
        )

        # Tuesday (2026-09-01)
        tuesday_dt = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.get_current_timezone())
        AttendanceSession.objects.create(
            program=self.program,
            student=self.student,
            check_in=tuesday_dt,
            check_out=tuesday_dt + timedelta(hours=3),
            duration_minutes=180,
        )

        # Wednesday (2026-09-02)
        wednesday_dt = datetime(
            2026, 9, 2, 10, 0, tzinfo=timezone.get_current_timezone()
        )
        AttendanceSession.objects.create(
            program=self.program,
            student=self.student,
            check_in=wednesday_dt,
            check_out=wednesday_dt + timedelta(hours=4),
            duration_minutes=240,
        )

        self.url = reverse("attendance_hours_chart")

    def test_filter_by_single_day(self):
        """Test filtering by Tuesday (3)."""
        response = self.client.get(
            self.url, {"program_id": self.program.id, "days_of_week": ["3"]}
        )
        self.assertEqual(response.status_code, 200)

        # The chart data should only include the 3 hours from Tuesday
        student_list = response.context["student_list"]
        self.assertEqual(len(student_list), 1)
        self.assertEqual(student_list[0]["total_hours"], 3.0)

    def test_filter_by_multiple_days(self):
        """Test filtering by Monday (2) and Wednesday (4)."""
        response = self.client.get(
            self.url, {"program_id": self.program.id, "days_of_week": ["2", "4"]}
        )
        self.assertEqual(response.status_code, 200)

        # The chart data should include 2 (Mon) + 4 (Wed) = 6 hours
        student_list = response.context["student_list"]
        self.assertEqual(len(student_list), 1)
        self.assertEqual(student_list[0]["total_hours"], 6.0)

    def test_filter_by_no_matching_days(self):
        """Test filtering by Thursday (5) where no sessions exist."""
        response = self.client.get(
            self.url, {"program_id": self.program.id, "days_of_week": ["5"]}
        )
        self.assertEqual(response.status_code, 200)

        # No sessions on Thursday
        student_list = response.context["student_list"]
        self.assertEqual(len(student_list), 0)
