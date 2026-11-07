from datetime import date, datetime, time, timedelta
from types import SimpleNamespace

from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from attendance.models import AttendanceSession
from organizations.models import Organization
from outreach import utils as outreach_utils
from outreach.models import OutreachShift, OutreachSignup
from outreach.tests.factories import create_outreach_event, record_full_attendance
from outreach.utils import get_student_outreach_stats
from programs.models import Adult, Enrollment, Program, ProgramFeature, School, Student


class OutreachAttendanceOverlapCalculationTests(TestCase):
    def at(self, hour, minute=0):
        return timezone.make_aware(datetime(2026, 8, 1, hour, minute))

    def signup(self, start, end, finalized=True):
        return SimpleNamespace(
            checked_in_at=start,
            checked_out_at=end,
            is_finalized=finalized,
        )

    def session(self, start, end):
        return SimpleNamespace(check_in=start, check_out=end)

    def test_overlap_unions_multiple_signups_and_sessions(self):
        signups = [
            self.signup(self.at(10), self.at(12)),
            self.signup(self.at(11), self.at(13)),
            self.signup(self.at(10), self.at(12), finalized=False),
        ]
        sessions = [
            self.session(self.at(9, 30), self.at(11)),
            self.session(self.at(10, 30), self.at(11, 30)),
            self.session(self.at(12, 30), self.at(14)),
            self.session(self.at(13), self.at(14)),
        ]

        overlap_hours = outreach_utils.compute_attendance_overlap_hours(
            signups, sessions
        )

        self.assertEqual(overlap_hours, 2.0)

    def test_boundary_touching_intervals_do_not_overlap(self):
        signups = [self.signup(self.at(10), self.at(11))]
        sessions = [self.session(self.at(9), self.at(10))]

        overlap_hours = outreach_utils.compute_attendance_overlap_hours(
            signups, sessions
        )

        self.assertEqual(overlap_hours, 0.0)

    def test_full_overlap_and_no_attendance(self):
        signups = [self.signup(self.at(10), self.at(12))]

        full_overlap_hours = outreach_utils.compute_attendance_overlap_hours(
            signups, [self.session(self.at(10), self.at(12))]
        )
        no_overlap_hours = outreach_utils.compute_attendance_overlap_hours(signups, [])

        self.assertEqual(full_overlap_hours, 2.0)
        self.assertEqual(no_overlap_hours, 0.0)

    def test_open_attendance_session_is_capped_at_now(self):
        signups = [self.signup(self.at(10), self.at(12))]
        sessions = [self.session(self.at(11), None)]

        overlap_hours = outreach_utils.compute_attendance_overlap_hours(
            signups, sessions, now=self.at(11, 30)
        )

        self.assertEqual(overlap_hours, 0.5)

    def test_incomplete_or_invalid_intervals_do_not_add_overlap(self):
        signups = [
            self.signup(self.at(10), None, finalized=False),
            self.signup(self.at(12), self.at(11)),
        ]
        sessions = [self.session(self.at(10), self.at(12))]

        overlap_hours = outreach_utils.compute_attendance_overlap_hours(
            signups, sessions
        )

        self.assertEqual(overlap_hours, 0.0)


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
        record_full_attendance(
            OutreachSignup.objects.get(
                student=self.student, shift=self.event1.shifts.first()
            ),
            self.event1.shifts.first(),
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
        record_full_attendance(
            OutreachSignup.objects.get(
                student=self.student, shift=self.event2.shifts.first()
            ),
            self.event2.shifts.first(),
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

    def test_stats_modal_uses_bulk_same_organization_sessions_across_programs(self):
        organization = self.program.organization
        attendance_program = Program.objects.create(
            name="Attendance Program", organization=organization
        )
        other_organization = Organization.objects.create(
            name="Other Organization", slug="other-organization"
        )
        other_program = Program.objects.create(
            name="Other Program", organization=other_organization
        )
        other_student = Student.objects.create(
            legal_first_name="Bea",
            last_name="Student",
            school=self.school,
            graduation_year=2027,
        )
        Enrollment.objects.create(
            student=other_student, program=self.program, active=True
        )
        other_signup = OutreachSignup.objects.create(
            student=other_student,
            shift=self.event1.shifts.first(),
            role=OutreachSignup.HELPER,
        )
        record_full_attendance(other_signup, self.event1.shifts.first())

        AttendanceSession.objects.create(
            program=attendance_program,
            student=self.student,
            organization=organization,
            check_in=self.at_time(10, 30),
            check_out=self.at_time(11, 30),
        )
        AttendanceSession.objects.create(
            program=attendance_program,
            student=other_student,
            organization=organization,
            check_in=self.at_time(10, 30),
            check_out=self.at_time(11, 30),
        )
        AttendanceSession.objects.create(
            program=other_program,
            student=self.student,
            organization=other_organization,
            check_in=self.at_time(10, 30),
            check_out=self.at_time(11, 30),
        )

        self.client.login(username="mentor", password="password")  # nosec B106
        url = reverse("outreach:student_stats", args=[self.program.id])
        with CaptureQueriesContext(connection) as captured_queries:
            resp = self.client.get(url)

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Overlap Hours")
        self.assertContains(resp, "1.0")
        student_stats = {stat["name"]: stat for stat in resp.context["student_stats"]}
        self.assertEqual(student_stats["Alice Zuberg"]["overlap_hours"], 1.0)
        self.assertEqual(student_stats["Alice Zuberg"]["hours"], 5.0)
        self.assertEqual(student_stats["Bea Student"]["overlap_hours"], 1.0)
        attendance_queries = [
            query
            for query in captured_queries.captured_queries
            if AttendanceSession._meta.db_table in query["sql"]
        ]
        self.assertEqual(len(attendance_queries), 1)

    def at_time(self, hour, minute=0):
        return timezone.make_aware(datetime(2026, 8, 1, hour, minute))


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
        record_full_attendance(
            OutreachSignup.objects.get(
                student=self.student, shift=self.past_event.shifts.first()
            ),
            self.past_event.shifts.first(),
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
