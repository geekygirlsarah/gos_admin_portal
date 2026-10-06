from datetime import datetime, timedelta

from django.contrib.auth.models import User
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

        # Create sessions on different days.
        # Django's `check_in__week_day` uses Sunday=1, Monday=2, Tuesday=3,
        # Wednesday=4, Thursday=5, Friday=6, Saturday=7, while Python's
        # `date.weekday()` uses Monday=0 ... Sunday=6.
        #
        # The hours-chart view defaults its date range to the selected
        # program's window (clamped to today), so these sessions must sit in
        # the recent past *relative to when the suite runs* — pinned calendar
        # dates eventually fall outside that rolling window and the filter
        # tests would match nothing.
        tz = timezone.get_current_timezone()
        today = timezone.localdate()

        # Anchor on the most recent completed Monday that is at least a week
        # back, so Monday/Tuesday/Wednesday all land inside the window.
        monday_date = today - timedelta(days=today.weekday() + 7)
        # Django week_day value for a given Python weekday (Mon=0 -> 2).
        self.monday_dow = monday_date.weekday() + 2
        self.tuesday_dow = monday_date.weekday() + 3
        self.wednesday_dow = monday_date.weekday() + 4
        self.thursday_dow = monday_date.weekday() + 5

        # Monday
        monday_dt = datetime(
            monday_date.year, monday_date.month, monday_date.day, 10, 0, tzinfo=tz
        )
        AttendanceSession.objects.create(
            program=self.program,
            student=self.student,
            check_in=monday_dt,
            check_out=monday_dt + timedelta(hours=2),
            duration_minutes=120,
        )

        # Tuesday
        tuesday_dt = monday_dt + timedelta(days=1)
        AttendanceSession.objects.create(
            program=self.program,
            student=self.student,
            check_in=tuesday_dt,
            check_out=tuesday_dt + timedelta(hours=3),
            duration_minutes=180,
        )

        # Wednesday
        wednesday_dt = monday_dt + timedelta(days=2)
        AttendanceSession.objects.create(
            program=self.program,
            student=self.student,
            check_in=wednesday_dt,
            check_out=wednesday_dt + timedelta(hours=4),
            duration_minutes=240,
        )

        self.url = reverse("attendance_hours_chart")

    def test_filter_by_single_day(self):
        """Test filtering by Tuesday."""
        response = self.client.get(
            self.url,
            {"program_id": self.program.id, "days_of_week": [self.tuesday_dow]},
        )
        self.assertEqual(response.status_code, 200)

        # The chart data should only include the 3 hours from Tuesday
        student_list = response.context["student_list"]
        self.assertEqual(len(student_list), 1)
        self.assertEqual(student_list[0]["total_hours"], 3.0)

    def test_filter_by_multiple_days(self):
        """Test filtering by Monday and Wednesday."""
        response = self.client.get(
            self.url,
            {
                "program_id": self.program.id,
                "days_of_week": [self.monday_dow, self.wednesday_dow],
            },
        )
        self.assertEqual(response.status_code, 200)

        # The chart data should include 2 (Mon) + 4 (Wed) = 6 hours
        student_list = response.context["student_list"]
        self.assertEqual(len(student_list), 1)
        self.assertEqual(student_list[0]["total_hours"], 6.0)

    def test_filter_by_no_matching_days(self):
        """Test filtering by Thursday, where no sessions exist."""
        response = self.client.get(
            self.url,
            {"program_id": self.program.id, "days_of_week": [self.thursday_dow]},
        )
        self.assertEqual(response.status_code, 200)

        # No sessions on Thursday
        student_list = response.context["student_list"]
        self.assertEqual(len(student_list), 0)
