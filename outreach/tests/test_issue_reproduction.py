from datetime import date, time, timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from outreach.models import OutreachEvent, OutreachSignup
from outreach.tests.factories import create_outreach_event
from programs.models import Adult, Enrollment, Program, ProgramFeature, School, Student


class OutreachStatsHoursTest(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Test School")
        self.feature, _ = ProgramFeature.objects.get_or_create(
            key="outreach", defaults={"name": "Outreach"}
        )
        self.program = Program.objects.create(name="Test Program")
        self.program.features.add(self.feature)

        self.student_user = User.objects.create_user(
            username="student1", password="password"
        )  # nosec B106
        self.student = Student.objects.create(
            user=self.student_user,
            legal_first_name="Alice",
            last_name="Zuberg",
            school=self.school,
            graduation_year=2027,
        )
        Enrollment.objects.create(
            student=self.student, program=self.program, active=True
        )

        today = timezone.now().date()

        # Past event (1 hour)
        self.past_event = create_outreach_event(
            program=self.program,
            name="Past Event",
            location_name="Loc 1",
            location_address="Addr 1",
            start_date=today - timedelta(days=2),
            start_time=time(10, 0),
            end_time=time(11, 0),
        )
        OutreachSignup.objects.create(
            student=self.student,
            shift=self.past_event.shifts.first(),
            role=OutreachSignup.CHAMPION,
        )

        # Upcoming event (2 hours)
        self.upcoming_event = create_outreach_event(
            program=self.program,
            name="Upcoming Event",
            location_name="Loc 2",
            location_address="Addr 2",
            start_date=today + timedelta(days=2),
            start_time=time(10, 0),
            end_time=time(12, 0),
        )
        OutreachSignup.objects.create(
            student=self.student,
            shift=self.upcoming_event.shifts.first(),
            role=OutreachSignup.HELPER,
        )

    def test_stats_include_upcoming_champions(self):
        """
        Verify that championed_count includes upcoming events,
        but total_outreach_hours only includes past ones.
        """
        today = timezone.now().date()
        upcoming_event = create_outreach_event(
            program=self.program,
            name="Upcoming Champ Event",
            location_name="Loc U",
            location_address="Addr U",
            start_date=today + timedelta(days=5),
            start_time=time(10, 0),
            end_time=time(12, 0),
        )
        OutreachSignup.objects.create(
            student=self.student,
            shift=upcoming_event.shifts.first(),
            role=OutreachSignup.CHAMPION,
        )

        self.client.login(username="student1", password="password")  # nosec B106
        url = reverse("outreach:event_list", args=[self.program.id])
        resp = self.client.get(url)

        # 1 past + 1 upcoming championed = 2
        self.assertContains(resp, "2")
        # Hours should still be 1.0 (past event only)
        self.assertContains(resp, "1.0")
        # Pending hours should be 4.0 (2h upcoming helper + 2h upcoming championed)
        self.assertContains(resp, "4.0")
