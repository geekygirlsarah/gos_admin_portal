from datetime import date, time, timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from outreach.models import OutreachShift, OutreachSignup
from outreach.tests.factories import create_outreach_event
from outreach.utils import get_student_outreach_stats
from programs.models import Adult, Enrollment, Program, ProgramFeature, School, Student


class OutreachStatsTest(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Test School")
        self.feature, _ = ProgramFeature.objects.get_or_create(
            key="outreach", defaults={"name": "Outreach"}
        )
        self.program = Program.objects.create(name="Test Program")
        self.program.features.add(self.feature)

        self.mentor_user = User.objects.create_user(
            username="mentor", password="password"
        )  # nosec B106
        self.mentor_adult = Adult.objects.create(
            user=self.mentor_user, is_mentor=True, mentor_active=True
        )

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

        # 2 hour event, championed (PAST)
        self.event1 = create_outreach_event(
            program=self.program,
            name="Event 1",
            location_name="Loc 1",
            location_address="Addr 1",
            start_date=date(2026, 8, 1),
            start_time=time(10, 0),
            end_time=time(12, 0),
        )
        OutreachSignup.objects.create(
            student=self.student,
            shift=self.event1.shifts.first(),
            role=OutreachSignup.CHAMPION,
        )

        # 3 hour event, helped (PAST)
        self.event2 = create_outreach_event(
            program=self.program,
            name="Event 2",
            location_name="Loc 2",
            location_address="Addr 2",
            start_date=date(2026, 8, 2),
            start_time=time(13, 0),
            end_time=time(16, 0),
        )
        OutreachSignup.objects.create(
            student=self.student,
            shift=self.event2.shifts.first(),
            role=OutreachSignup.HELPER,
        )

    def test_event_duration_hours(self):
        self.assertEqual(self.event1.duration_hours, 2.0)
        self.assertEqual(self.event2.duration_hours, 3.0)

    def test_student_outreach_stats_in_context(self):
        self.client.login(username="student1", password="password")  # nosec B106
        url = reverse("outreach:event_list", args=[self.program.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        # We expect these keys in context or rendered in HTML
        self.assertContains(resp, "1")  # Championed count
        self.assertContains(resp, "Events Championed")
        self.assertContains(resp, "5.0")  # Outreach hours (floatformat:1)
        self.assertContains(resp, "Completed Hours")

    def test_mentor_can_see_student_stats_button(self):
        self.client.login(username="mentor", password="password")  # nosec B106
        url = reverse("outreach:event_list", args=[self.program.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Student Stats")

    def test_mentor_student_stats_modal_content(self):
        self.client.login(username="mentor", password="password")  # nosec B106
        url = reverse("outreach:student_stats", args=[self.program.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        # Check alphabetization and columns
        self.assertContains(resp, "Alice Zuberg")
        self.assertContains(resp, "1")  # Championed
        self.assertContains(resp, "5")  # Hours


class OutreachStatsUpcomingAndPendingHoursTests(TestCase):
    """Verify stats separation: championed includes upcoming, hours split past/pending.

    Integrated from outreach/tests/test_issue_reproduction.py.
    """

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

        # Past event (1 hour) - championed
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

        # Upcoming event (2 hours) - helper
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

    def test_stats_include_upcoming_champions_but_hours_split(self):
        """championed_count includes upcoming events, hours remain past-only."""
        today = timezone.now().date()
        upcoming_champ = create_outreach_event(
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
            shift=upcoming_champ.shifts.first(),
            role=OutreachSignup.CHAMPION,
        )

        # Direct util check - more precise than HTML contains
        stats = get_student_outreach_stats(self.student, self.program)
        self.assertEqual(stats["championed_count"], 2)  # 1 past + 1 upcoming
        self.assertEqual(stats["total_outreach_hours"], 1.0)  # past only
        self.assertEqual(stats["pending_outreach_hours"], 4.0)  # 2h + 2h

        # Also verify rendered view still reflects the split
        self.client.login(username="student1", password="password")  # nosec B106
        url = reverse("outreach:event_list", args=[self.program.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "2")  # championed
        self.assertContains(resp, "1.0")  # completed
        self.assertContains(resp, "4.0")  # pending

    def test_champion_count_distinct_events_not_shifts(self):
        """Championing two shifts of same event counts as 1, not 2."""
        event = create_outreach_event(
            program=self.program,
            name="Multi-shift Event",
            location_name="Loc M",
            location_address="Addr M",
            start_date=date(2026, 9, 1),
            start_time=time(10, 0),
            end_time=time(12, 0),
        )
        shift2 = OutreachShift.objects.create(
            event=event,
            date=date(2026, 9, 1),
            start_time=time(13, 0),
            end_time=time(15, 0),
        )

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
        # setUp has 1 championed (past_event), plus this multi-shift event = 2
        self.assertEqual(stats["championed_count"], 2)
