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

    def test_champion_count_multiple_shifts_one_event(self):
        """
        If a student champions two shifts at the same event, it should only count as 1 credit.
        """
        from outreach.models import OutreachShift
        from outreach.utils import get_student_outreach_stats

        # Create an event with two shifts
        event = create_outreach_event(
            program=self.program,
            name="Multi-shift Event",
            location_name="Loc M",
            location_address="Addr M",
            start_date=date(2026, 9, 1),
            start_time=time(10, 0),
            end_time=time(12, 0),
        )
        # Add a second shift to the same event
        shift2 = OutreachShift.objects.create(
            event=event,
            date=date(2026, 9, 1),
            start_time=time(13, 0),
            end_time=time(15, 0),
        )

        # Student champions both shifts
        OutreachSignup.objects.create(
            student=self.student,
            shift=event.shifts.first(),
            role=OutreachSignup.CHAMPION,
        )
        OutreachSignup.objects.create(
            student=self.student,
            shift=shift2,
            role=OutreachSignup.CHAMPION,
        )

        stats = get_student_outreach_stats(self.student, self.program)

        # Currently this will fail and be 2 because it counts signups.
        # We want it to be 1 because it's the same event.
        # (Note: Alice already has 1 championed from setUp, and 1 from test_stats_include_upcoming_champions if it ran,
        # but this is a fresh test case, so only the ones from setUp count.)
        # SetUp has: self.past_event (championed) -> 1
        # Plus this event (2 shifts) -> should be 1
        # Total should be 2.
        self.assertEqual(stats["championed_count"], 2)
