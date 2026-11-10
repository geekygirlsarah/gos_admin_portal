"""Tests for the weekly automated digest service and management command."""

import datetime
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core import mail
from django.core.management import call_command
from django.db.models.signals import post_init
from django.test import TestCase
from django.utils import timezone

from attendance.models import AttendanceSession
from calendar_feeds.models import (
    CalendarEvent,
    CalendarFeed,
    CalendarFeedACL,
    EventException,
)
from GoSAdminPortal.test_runner import _assign_default_test_org
from organizations.models import Organization, UserOrganizationMembership
from organizations.utils import (
    get_organization_email_settings,
    update_organization_email_settings,
)
from outreach.models import (
    OutreachEvent,
    OutreachMentorSignup,
    OutreachShift,
    OutreachSignup,
)
from programs.models import (
    Adult,
    AdultStudentRelationship,
    Enrollment,
    Fee,
    MentorAgreement,
    MentorAgreementAcceptance,
    Program,
    ProgramDocument,
    ProgramFeature,
    Student,
    StudentDocument,
)
from programs.services.digest import (
    collect_mentor_digest_data,
    collect_parent_digest_data,
    collect_student_digest_data,
    get_upcoming_calendar_events,
    get_weekly_digest_recipients,
)
from travel.models import (
    TravelEvent,
    TravelMentorSignup,
    TravelParentChaperoneSignup,
    TravelSignup,
)


class WeeklyDigestServiceTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.program = Program.objects.create(
            name="Robotics Season 2026",
            active=True,
            start_date=self.today - datetime.timedelta(days=30),
            end_date=self.today + datetime.timedelta(days=90),
        )
        f_outreach, _ = ProgramFeature.objects.get_or_create(
            key="outreach", defaults={"name": "Outreach"}
        )
        f_attendance, _ = ProgramFeature.objects.get_or_create(
            key="attendance", defaults={"name": "Attendance"}
        )
        self.program.features.add(f_outreach, f_attendance)

        # Create student and user
        self.student_user = User.objects.create_user(
            username="student1", email="student1@example.com"
        )
        self.student = Student.objects.create(
            legal_first_name="Jane",
            preferred_first_name="Jane",
            last_name="Doe",
            user=self.student_user,
            personal_email="student1@example.com",
        )
        Enrollment.objects.create(
            student=self.student, program=self.program, active=True
        )

        # Create parent and user
        self.parent_user = User.objects.create_user(
            username="parent1", email="parent1@example.com"
        )
        self.parent = Adult.objects.create(
            legal_first_name="John",
            last_name="Doe",
            is_parent=True,
            personal_email="parent1@example.com",
            email_updates=True,
            user=self.parent_user,
        )
        AdultStudentRelationship.objects.create(
            adult=self.parent, student=self.student, relationship_to_student="parent"
        )
        self.student.primary_contact = self.parent
        self.student.save()

        # Create second student for parent who is not enrolled in any program
        self.student2 = Student.objects.create(
            legal_first_name="Alice",
            preferred_first_name="Alice",
            last_name="Doe",
            personal_email="student2@example.com",
            date_of_birth=datetime.date(2012, 1, 1),
        )
        AdultStudentRelationship.objects.create(
            adult=self.parent, student=self.student2, relationship_to_student="parent"
        )

        # Create mentor and user
        self.mentor_user = User.objects.create_user(
            username="mentor1", email="mentor1@example.com"
        )
        self.mentor = Adult.objects.create(
            legal_first_name="Mentor",
            last_name="Smith",
            is_mentor=True,
            mentor_active=True,
            personal_email="mentor1@example.com",
            user=self.mentor_user,
        )

        # Create outreach events & shifts
        self.event1 = OutreachEvent.objects.create(
            program=self.program,
            name="Community STEM Fair",
            location_name="Carnegie Library",
            location_address="4400 Forbes Ave, Pittsburgh, PA",
        )
        self.shift1 = OutreachShift.objects.create(
            event=self.event1,
            date=self.today + datetime.timedelta(days=2),
            start_time=datetime.time(10, 0),
            end_time=datetime.time(12, 0),
            max_champions=1,
            max_helpers=3,
        )
        self.shift2 = OutreachShift.objects.create(
            event=self.event1,
            date=self.today + datetime.timedelta(days=2),
            start_time=datetime.time(12, 0),
            end_time=datetime.time(14, 0),
            max_champions=1,
            max_helpers=3,
        )

    def test_student_digest_data_collection(self):
        # Sign up student for shift1 as Champion
        OutreachSignup.objects.create(
            student=self.student, shift=self.shift1, role=OutreachSignup.CHAMPION
        )

        # Log attendance
        AttendanceSession.objects.create(
            program=self.program,
            student=self.student,
            check_in=timezone.now() - datetime.timedelta(hours=2),
            check_out=timezone.now(),
            duration_minutes=120,
        )

        data = collect_student_digest_data(
            self.student, days_ahead=7, reference_date=self.today
        )

        self.assertTrue(data["has_content"])
        self.assertEqual(len(data["confirmed_shifts"]), 1)
        self.assertEqual(
            data["confirmed_shifts"][0]["event_name"], "Community STEM Fair"
        )
        self.assertEqual(data["confirmed_shifts"][0]["role_display"], "Champion")
        self.assertEqual(
            data["confirmed_shifts"][0]["location_name"], "Carnegie Library"
        )

        # Open opportunities should list shift2 (since student is not signed up for it)
        self.assertEqual(len(data["open_opportunities"]), 1)
        self.assertEqual(
            data["open_opportunities"][0]["event_name"], "Community STEM Fair"
        )

        # Attendance stats
        self.assertGreater(data["attendance_stats"]["total_hours"], 0)

    def test_parent_digest_data_collection(self):
        OutreachSignup.objects.create(
            student=self.student, shift=self.shift1, role=OutreachSignup.HELPER
        )

        data = collect_parent_digest_data(
            self.parent, days_ahead=7, reference_date=self.today
        )

        self.assertTrue(data["has_content"])
        self.assertEqual(len(data["students_data"]), 1)
        student_data = data["students_data"][0]
        self.assertEqual(student_data["student"], self.student)
        self.assertEqual(len(student_data["confirmed_shifts"]), 1)
        self.assertEqual(student_data["confirmed_shifts"][0]["role_display"], "Helper")

    def test_mentor_digest_data_collection(self):
        # Mentor signs up for shift1
        OutreachMentorSignup.objects.create(adult=self.mentor, shift=self.shift1)

        data = collect_mentor_digest_data(
            self.mentor, days_ahead=7, reference_date=self.today
        )

        self.assertTrue(data["has_content"])
        self.assertEqual(len(data["confirmed_shifts"]), 1)
        self.assertEqual(
            data["confirmed_shifts"][0]["event_name"], "Community STEM Fair"
        )

        # shift2 needs a mentor (0 signed up)
        self.assertGreaterEqual(len(data["needs_support_shifts"]), 1)

    def test_mentor_digest_excludes_foreign_outreach_travel_and_attendance(self):
        foreign_org = Organization.objects.create(
            name="Digest Beta", slug="digest-beta", subdomain="digest-beta"
        )
        foreign_program = Program.objects.create(
            organization=foreign_org,
            name="Beta Confidential Program",
            active=True,
            start_date=self.today - datetime.timedelta(days=30),
        )
        attendance_feature = ProgramFeature.objects.create(
            organization=foreign_org,
            key="attendance",
            name="Attendance",
        )
        foreign_program.features.add(attendance_feature)
        AttendanceSession.objects.create(
            organization=foreign_org,
            program=foreign_program,
            adult=self.mentor,
            check_in=timezone.now() - datetime.timedelta(hours=2),
            check_out=timezone.now(),
            duration_minutes=120,
        )
        AttendanceSession.objects.create(
            organization=foreign_org,
            program=self.program,
            adult=self.mentor,
            check_in=timezone.now() - datetime.timedelta(hours=2),
            check_out=timezone.now(),
            duration_minutes=120,
        )
        foreign_event = OutreachEvent.objects.create(
            organization=foreign_org,
            program=foreign_program,
            name="Beta Confidential Outreach",
            location_name="Beta Venue",
            location_address="Beta Address",
        )
        foreign_shift = OutreachShift.objects.create(
            organization=foreign_org,
            event=foreign_event,
            date=self.today + datetime.timedelta(days=3),
            start_time=datetime.time(10, 0),
            end_time=datetime.time(12, 0),
        )
        OutreachMentorSignup.objects.create(
            organization=foreign_org,
            adult=self.mentor,
            shift=foreign_shift,
        )
        foreign_trip = TravelEvent.objects.create(
            organization=foreign_org,
            program=foreign_program,
            name="Beta Confidential Trip",
            start_date=self.today + datetime.timedelta(days=3),
            end_date=self.today + datetime.timedelta(days=4),
        )
        TravelMentorSignup.objects.create(
            organization=foreign_org,
            adult=self.mentor,
            event=foreign_trip,
        )

        data = collect_mentor_digest_data(
            self.mentor, days_ahead=7, reference_date=self.today
        )

        shift_names = {
            shift["event_name"]
            for shift in data["confirmed_shifts"] + data["needs_support_shifts"]
        }
        self.assertNotIn("Beta Confidential Outreach", shift_names)
        self.assertNotIn(
            "Beta Confidential Trip",
            {trip["event_name"] for trip in data["travel_highlights"]},
        )
        self.assertNotIn(
            foreign_program,
            [row["program"] for row in data["attendance_stats"]["by_program"]],
        )
        self.assertEqual(data["attendance_stats"]["recent_days"], 0)

    def test_empty_state_suppression(self):
        # Another student with no shifts, no open opportunities (e.g. past events only), no attendance
        s2 = Student.objects.create(
            legal_first_name="Bob",
            last_name="Builder",
            personal_email="bob@example.com",
        )
        data = collect_student_digest_data(s2, days_ahead=7, reference_date=self.today)
        self.assertFalse(data["has_content"])

    def test_parent_strictly_requires_opt_in(self):
        # Parents only get the digest if they've checked email_updates=True
        self.parent.email_updates = False
        self.parent.save()

        recipients = get_weekly_digest_recipients(role="parent")
        parent_emails = [r["email"] for r in recipients]
        self.assertNotIn("parent1@example.com", parent_emails)

        self.parent.email_updates = True
        self.parent.save()
        recipients = get_weekly_digest_recipients(role="parent")
        parent_emails = [r["email"] for r in recipients]
        self.assertIn("parent1@example.com", parent_emails)

    def test_inactive_organization_has_no_weekly_digest_recipients(self):
        organization = self.student.organization
        organization.is_active = False
        organization.save(update_fields=["is_active"])

        recipients = get_weekly_digest_recipients(role="all")

        self.assertEqual(recipients, [])

    def test_parent_digest_excludes_unenrolled_student_without_content(self):
        # Student 1 is enrolled with an upcoming shift
        data = collect_parent_digest_data(
            self.parent, days_ahead=7, reference_date=self.today
        )
        student_names = [sd["student"].display_name for sd in data["students_data"]]
        self.assertIn("Jane Doe", student_names)
        self.assertNotIn("Alice Doe", student_names)

    def test_student_digest_attendance_stats_and_averages(self):
        # Create attendance sessions across past days
        AttendanceSession.objects.create(
            program=self.program,
            student=self.student,
            check_in=timezone.now() - datetime.timedelta(days=2, hours=3),
            check_out=timezone.now() - datetime.timedelta(days=2),
            duration_minutes=180,
        )
        AttendanceSession.objects.create(
            program=self.program,
            student=self.student,
            check_in=timezone.now() - datetime.timedelta(days=1, hours=2),
            check_out=timezone.now() - datetime.timedelta(days=1),
            duration_minutes=120,
        )

        data = collect_student_digest_data(
            self.student, days_ahead=7, reference_date=self.today
        )

        stats = data["attendance_stats"]
        self.assertEqual(stats["total_hours"], 5.0)
        self.assertIn("avg_hours_per_week", stats)
        self.assertGreater(stats["avg_hours_per_week"], 0)
        self.assertEqual(stats["recent_days"], 2)
        self.assertEqual(stats["recent_hours"], 5.0)

    def test_student_digest_outreach_summary(self):
        # Create past finalized shift
        past_event = OutreachEvent.objects.create(
            program=self.program,
            name="Past Workshop",
            location_name="High School",
        )
        past_shift = OutreachShift.objects.create(
            event=past_event,
            date=self.today - datetime.timedelta(days=5),
            start_time=datetime.time(10, 0),
            end_time=datetime.time(13, 0),
            max_champions=1,
            max_helpers=3,
        )
        OutreachSignup.objects.create(
            student=self.student,
            shift=past_shift,
            role=OutreachSignup.CHAMPION,
            checked_in_at=timezone.now() - datetime.timedelta(days=5, hours=3),
            checked_out_at=timezone.now() - datetime.timedelta(days=5),
        )

        data = collect_student_digest_data(
            self.student, days_ahead=7, reference_date=self.today
        )

        outreach_summary = data.get("outreach_summary", {})
        self.assertEqual(outreach_summary.get("total_hours"), 3.0)
        self.assertEqual(outreach_summary.get("completed_events"), 1)
        self.assertEqual(outreach_summary.get("championed_count"), 1)

    def test_calendar_events_included_in_digest(self):
        feed = CalendarFeed.objects.create(
            name="Team Calendar",
            slug="team-calendar",
            visibility=CalendarFeed.VISIBILITY_ANONYMOUS,
            source_type=CalendarFeed.SOURCE_MANUAL,
            is_active=True,
        )
        CalendarEvent.objects.create(
            feed=feed,
            title="Team All-Hands Meeting",
            start_datetime=timezone.now() + datetime.timedelta(days=2),
            end_datetime=timezone.now() + datetime.timedelta(days=2, hours=1),
            location="Room 101",
        )

        data = collect_student_digest_data(
            self.student, days_ahead=7, reference_date=self.today
        )
        self.assertTrue(len(data.get("calendar_events", [])) >= 1)
        self.assertEqual(data["calendar_events"][0]["title"], "Team All-Hands Meeting")

        mentor_data = collect_mentor_digest_data(
            self.mentor, days_ahead=7, reference_date=self.today
        )
        self.assertTrue(len(mentor_data.get("calendar_events", [])) >= 1)
        self.assertEqual(
            mentor_data["calendar_events"][0]["title"], "Team All-Hands Meeting"
        )

    def test_student_and_mentor_action_items(self):
        # Add fee with balance due
        f_finances, _ = ProgramFeature.objects.get_or_create(
            key="finances", defaults={"name": "Finances"}
        )
        self.program.features.add(f_finances)
        Fee.objects.create(
            program=self.program,
            name="Registration Fee",
            amount=100.00,
        )

        # Add program document that is unsubmitted
        ProgramDocument.objects.create(
            program=self.program,
            name="Code of Conduct",
        )

        data = collect_student_digest_data(
            self.student, days_ahead=7, reference_date=self.today
        )
        action_items = data.get("action_items", {})
        self.assertEqual(action_items.get("total_balance_due"), 100.0)
        self.assertEqual(len(action_items.get("missing_documents", [])), 1)

        # Mentor pending agreement
        ag = MentorAgreement.objects.create(
            slug="safety-policy",
            title="Safety Policy 2026",
            version=1,
            effective_date=self.today,
            is_active=True,
            content="Safety rules content",
        )
        mentor_data = collect_mentor_digest_data(
            self.mentor, days_ahead=7, reference_date=self.today
        )
        mentor_actions = mentor_data.get("action_items", {})
        self.assertTrue(mentor_actions.get("has_pending_agreements"))


class WeeklyDigestCalendarTenantIsolationTests(TestCase):
    tenant_models = (
        Adult,
        AdultStudentRelationship,
        CalendarEvent,
        CalendarFeed,
        CalendarFeedACL,
        Enrollment,
        EventException,
        Program,
        Student,
    )

    def setUp(self):
        self.disconnected_models = []
        for model in self.tenant_models:
            if post_init.disconnect(
                receiver=_assign_default_test_org,
                sender=model,
                dispatch_uid=f"default-test-org-{model._meta.label}",
            ):
                self.disconnected_models.append(model)
        self.addCleanup(self._restore_default_organization_hook)

        self.today = timezone.localdate()
        self.alpha = Organization.objects.create(
            name="Digest Calendar Alpha",
            slug="digest-calendar-alpha",
            subdomain="digest-calendar-alpha",
        )
        self.beta = Organization.objects.create(
            name="Digest Calendar Beta",
            slug="digest-calendar-beta",
            subdomain="digest-calendar-beta",
        )

        self.student_user = User.objects.create_user(
            username="digest-calendar-student", email="student@example.test"
        )
        self.student = Student.objects.create(
            organization=self.alpha,
            user=self.student_user,
            legal_first_name="Local",
            last_name="Student",
        )
        self.parent_user = User.objects.create_user(
            username="digest-calendar-parent", email="parent@example.test"
        )
        self.parent = Adult.objects.create(
            organization=self.alpha,
            user=self.parent_user,
            legal_first_name="Local",
            last_name="Parent",
            is_parent=True,
        )
        AdultStudentRelationship.objects.create(
            organization=self.alpha,
            adult=self.parent,
            student=self.student,
            relationship_to_student="parent",
        )
        self.mentor_user = User.objects.create_user(
            username="digest-calendar-mentor", email="mentor@example.test"
        )
        self.mentor = Adult.objects.create(
            organization=self.alpha,
            user=self.mentor_user,
            legal_first_name="Local",
            last_name="Mentor",
            is_mentor=True,
            mentor_active=True,
        )
        for user, role in (
            (self.student_user, "Student"),
            (self.parent_user, "Parent"),
            (self.mentor_user, "Mentor"),
        ):
            UserOrganizationMembership.objects.update_or_create(
                user=user,
                organization=self.alpha,
                role=role,
                defaults={"is_active": True},
            )

        self.local_public_feed = self._create_feed(
            self.alpha, "Local Public", CalendarFeed.VISIBILITY_ANONYMOUS
        )
        self.local_members_feed = self._create_feed(
            self.alpha, "Local Members", CalendarFeed.VISIBILITY_MEMBERS
        )
        self.mentor_only_feed = self._create_feed(
            self.alpha, "Mentor Only", CalendarFeed.VISIBILITY_MEMBERS
        )
        self.foreign_public_feed = self._create_feed(
            self.beta, "Foreign Public", CalendarFeed.VISIBILITY_ANONYMOUS
        )
        for role in ("Student", "Parent", "Mentor"):
            CalendarFeedACL.objects.create(
                organization=self.alpha,
                feed=self.local_members_feed,
                role=role,
                can_read=True,
            )
        CalendarFeedACL.objects.create(
            organization=self.alpha,
            feed=self.mentor_only_feed,
            role="Mentor",
            can_read=True,
        )

        self._create_event(self.local_public_feed, "Local public event", self.alpha)
        self._create_event(self.local_members_feed, "Local members event", self.alpha)
        self._create_event(self.mentor_only_feed, "Mentor-only event", self.alpha)
        self._create_event(self.foreign_public_feed, "Foreign public event", self.beta)

        self.foreign_event_feed = self._create_feed(
            self.alpha,
            "Local feed with foreign event",
            CalendarFeed.VISIBILITY_ANONYMOUS,
        )
        self._create_event(
            self.foreign_event_feed, "Foreign-owned child event", self.beta
        )
        self.foreign_exception_feed = self._create_feed(
            self.alpha,
            "Local feed with foreign exception",
            CalendarFeed.VISIBILITY_ANONYMOUS,
        )
        recurring_event = self._create_event(
            self.foreign_exception_feed,
            "Event with foreign exception",
            self.alpha,
            recurring=True,
        )
        EventException.objects.create(
            organization=self.beta,
            event=recurring_event,
            original_date=self.today + datetime.timedelta(days=2),
            change_type=EventException.CHANGE_DELETED,
        )

    def _restore_default_organization_hook(self):
        for model in self.disconnected_models:
            post_init.connect(
                receiver=_assign_default_test_org,
                sender=model,
                dispatch_uid=f"default-test-org-{model._meta.label}",
            )

    def _create_feed(self, organization, name, visibility):
        slug = name.lower().replace(" ", "-")
        return CalendarFeed.objects.create(
            organization=organization,
            name=name,
            slug=slug,
            visibility=visibility,
            source_type=CalendarFeed.SOURCE_MANUAL,
            is_active=True,
        )

    def _create_event(self, feed, title, organization, *, recurring=False):
        start = timezone.make_aware(
            datetime.datetime.combine(
                self.today + datetime.timedelta(days=1), datetime.time(10, 0)
            ),
            timezone.get_current_timezone(),
        )
        return CalendarEvent.objects.create(
            organization=organization,
            feed=feed,
            title=title,
            start_datetime=start,
            end_datetime=start + datetime.timedelta(hours=1),
            rrule_ical="FREQ=DAILY;COUNT=5" if recurring else "",
        )

    @staticmethod
    def _titles(data):
        return {event["title"] for event in data["calendar_events"]}

    def test_student_parent_and_mentor_digests_use_local_readable_feeds(self):
        student_data = collect_student_digest_data(
            self.student, days_ahead=7, reference_date=self.today
        )
        parent_data = collect_parent_digest_data(
            self.parent, days_ahead=7, reference_date=self.today
        )
        mentor_data = collect_mentor_digest_data(
            self.mentor, days_ahead=7, reference_date=self.today
        )

        student_titles = self._titles(student_data)
        parent_titles = self._titles(parent_data)
        mentor_titles = self._titles(mentor_data)
        self.assertIn("Local public event", student_titles)
        self.assertIn("Local members event", student_titles)
        self.assertIn("Local public event", parent_titles)
        self.assertIn("Local members event", parent_titles)
        self.assertIn("Local public event", mentor_titles)
        self.assertIn("Local members event", mentor_titles)
        self.assertNotIn("Mentor-only event", student_titles)
        self.assertNotIn("Mentor-only event", parent_titles)
        self.assertIn("Mentor-only event", mentor_titles)

        for titles in (student_titles, parent_titles, mentor_titles):
            self.assertNotIn("Foreign public event", titles)
            self.assertNotIn("Foreign-owned child event", titles)
            self.assertNotIn("Event with foreign exception", titles)

    def test_foreign_child_feeds_are_skipped_before_event_expansion(self):
        expanded_feed_ids = []
        original_expander = CalendarFeed.get_events_for_date_range

        def record_expansion(feed, start_date, end_date):
            expanded_feed_ids.append(feed.pk)
            return original_expander(feed, start_date, end_date)

        with patch.object(
            CalendarFeed,
            "get_events_for_date_range",
            autospec=True,
            side_effect=record_expansion,
        ):
            data = collect_student_digest_data(
                self.student, days_ahead=7, reference_date=self.today
            )

        self.assertNotIn(self.foreign_event_feed.pk, expanded_feed_ids)
        self.assertNotIn(self.foreign_exception_feed.pk, expanded_feed_ids)
        self.assertIn(self.local_public_feed.pk, expanded_feed_ids)
        self.assertNotIn("Foreign-owned child event", self._titles(data))
        self.assertNotIn("Event with foreign exception", self._titles(data))

    def test_missing_organization_returns_no_events_for_any_digest_role(self):
        for role in ("Student", "Parent", "Mentor"):
            with self.subTest(role=role):
                self.assertEqual(
                    get_upcoming_calendar_events(
                        organization=None,
                        role=role,
                        reference_date=self.today,
                    ),
                    [],
                )

    def test_user_without_account_only_sees_public_calendar_events(self):
        events = get_upcoming_calendar_events(
            organization=self.alpha,
            role="Student",
            days_ahead=7,
            reference_date=self.today,
        )

        titles = {event["title"] for event in events}
        self.assertIn("Local public event", titles)
        self.assertNotIn("Local members event", titles)
        self.assertNotIn("Mentor-only event", titles)


class WeeklyDigestRelationshipTenantIsolationTests(TestCase):
    tenant_models = (
        Adult,
        AdultStudentRelationship,
        CalendarEvent,
        CalendarFeed,
        CalendarFeedACL,
        Enrollment,
        EventException,
        Fee,
        OutreachEvent,
        OutreachShift,
        OutreachSignup,
        Program,
        ProgramDocument,
        ProgramFeature,
        Student,
        StudentDocument,
        TravelEvent,
        TravelParentChaperoneSignup,
        TravelSignup,
    )

    def setUp(self):
        self.disconnected_models = []
        for model in self.tenant_models:
            if post_init.disconnect(
                receiver=_assign_default_test_org,
                sender=model,
                dispatch_uid=f"default-test-org-{model._meta.label}",
            ):
                self.disconnected_models.append(model)
        self.addCleanup(self._restore_default_organization_hook)

        self.today = timezone.localdate()
        self.alpha = Organization.objects.create(
            name="Digest Data Alpha",
            slug="digest-data-alpha",
            subdomain="digest-data-alpha",
        )
        self.beta = Organization.objects.create(
            name="Digest Data Beta",
            slug="digest-data-beta",
            subdomain="digest-data-beta",
        )
        self.student_user = User.objects.create_user(
            username="digest-data-student", email="digest-student@example.test"
        )
        self.student = Student.objects.create(
            organization=self.alpha,
            user=self.student_user,
            legal_first_name="Local",
            last_name="Student",
            personal_email="digest-student@example.test",
        )
        self.parent_user = User.objects.create_user(
            username="digest-data-parent", email="digest-parent@example.test"
        )
        self.parent = Adult.objects.create(
            organization=self.alpha,
            user=self.parent_user,
            legal_first_name="Local",
            last_name="Parent",
            personal_email="digest-parent@example.test",
            is_parent=True,
            email_updates=True,
        )
        AdultStudentRelationship.objects.create(
            organization=self.alpha,
            adult=self.parent,
            student=self.student,
            relationship_to_student="parent",
        )
        self.local_program = self._create_program(self.alpha, "Alpha Program")
        self.local_enrollment = Enrollment.objects.create(
            organization=self.alpha,
            student=self.student,
            program=self.local_program,
            active=True,
        )

    def _restore_default_organization_hook(self):
        for model in self.disconnected_models:
            post_init.connect(
                receiver=_assign_default_test_org,
                sender=model,
                dispatch_uid=f"default-test-org-{model._meta.label}",
            )

    def _create_program(self, organization, name):
        return Program.objects.create(
            organization=organization,
            name=name,
            active=True,
            start_date=self.today - datetime.timedelta(days=1),
            end_date=self.today + datetime.timedelta(days=90),
        )

    @staticmethod
    def _enable_feature(program, key):
        feature, _ = ProgramFeature.objects.get_or_create(
            organization=program.organization,
            key=key,
            defaults={"name": key.title()},
        )
        program.features.add(feature)

    def _create_calendar_event(self, title):
        feed = CalendarFeed.objects.create(
            organization=self.alpha,
            name=f"{title} Feed",
            slug=f"{title.lower().replace(' ', '-')}-feed",
            visibility=CalendarFeed.VISIBILITY_ANONYMOUS,
            source_type=CalendarFeed.SOURCE_MANUAL,
        )
        start = timezone.make_aware(
            datetime.datetime.combine(
                self.today + datetime.timedelta(days=2), datetime.time(10, 0)
            ),
            timezone.get_current_timezone(),
        )
        CalendarEvent.objects.create(
            organization=self.alpha,
            feed=feed,
            title=title,
            start_datetime=start,
            end_datetime=start + datetime.timedelta(hours=1),
        )

    def test_recipients_and_parent_children_require_local_active_enrollment(self):
        foreign_parent_user = User.objects.create_user(
            username="digest-foreign-parent", email="foreign-parent@example.test"
        )
        foreign_parent = Adult.objects.create(
            organization=self.beta,
            user=foreign_parent_user,
            legal_first_name="Foreign",
            last_name="Parent",
            is_parent=True,
            email_updates=True,
        )
        AdultStudentRelationship.objects.create(
            organization=self.alpha,
            adult=foreign_parent,
            student=self.student,
            relationship_to_student="parent",
        )
        self._create_calendar_event("Local enrollment event")
        Enrollment.objects.filter(pk=self.local_enrollment.pk).update(
            organization=self.beta
        )

        student_recipients = get_weekly_digest_recipients(role="student")
        parent_recipients = get_weekly_digest_recipients(role="parent")
        parent_data = collect_parent_digest_data(
            self.parent, days_ahead=7, reference_date=self.today
        )

        student_emails = {target["email"] for target in student_recipients}
        parent_emails = {target["email"] for target in parent_recipients}
        self.assertNotIn("digest-student@example.test", student_emails)
        self.assertNotIn("digest-parent@example.test", parent_emails)
        self.assertNotIn("foreign-parent@example.test", parent_emails)
        self.assertEqual(parent_data["students_data"], [])

    def test_student_digest_validates_outreach_and_travel_ownership_chains(self):
        foreign_program = self._create_program(self.beta, "Beta Program")
        foreign_student = Student.objects.create(
            organization=self.beta,
            legal_first_name="Foreign",
            last_name="Student",
        )
        local_event = OutreachEvent.objects.create(
            organization=self.alpha,
            program=self.local_program,
            name="Local Outreach",
            location_name="Alpha Hall",
            location_address="Alpha Address",
        )
        local_shift = OutreachShift.objects.create(
            organization=self.alpha,
            event=local_event,
            date=self.today + datetime.timedelta(days=3),
            start_time=datetime.time(10, 0),
            end_time=datetime.time(12, 0),
            max_champions=1,
            max_helpers=0,
        )
        OutreachSignup.objects.create(
            organization=self.beta,
            student=foreign_student,
            shift=local_shift,
            role=OutreachSignup.CHAMPION,
        )
        foreign_event = OutreachEvent.objects.create(
            organization=self.beta,
            program=foreign_program,
            name="Beta Confidential Outreach",
            location_name="Beta Hall",
            location_address="Beta Address",
        )
        foreign_shift = OutreachShift.objects.create(
            organization=self.beta,
            event=foreign_event,
            date=self.today + datetime.timedelta(days=4),
            start_time=datetime.time(10, 0),
            end_time=datetime.time(12, 0),
        )
        OutreachSignup.objects.create(
            organization=self.beta,
            student=self.student,
            shift=foreign_shift,
            role=OutreachSignup.HELPER,
        )

        foreign_trip = TravelEvent.objects.create(
            organization=self.beta,
            program=foreign_program,
            name="Beta Confidential Trip",
            start_date=self.today + datetime.timedelta(days=5),
            end_date=self.today + datetime.timedelta(days=6),
        )
        TravelSignup.objects.create(
            organization=self.beta,
            student=self.student,
            event=foreign_trip,
        )
        local_event_with_foreign_program = TravelEvent.objects.create(
            organization=self.alpha,
            program=foreign_program,
            name="Mismatched Program Trip",
            start_date=self.today + datetime.timedelta(days=7),
            end_date=self.today + datetime.timedelta(days=8),
        )
        TravelSignup.objects.create(
            organization=self.alpha,
            student=self.student,
            event=local_event_with_foreign_program,
        )
        TravelParentChaperoneSignup.objects.create(
            organization=self.beta,
            adult=self.parent,
            event=foreign_trip,
        )
        local_trip = TravelEvent.objects.create(
            organization=self.alpha,
            program=self.local_program,
            name="Local Trip",
            start_date=self.today + datetime.timedelta(days=5),
            end_date=self.today + datetime.timedelta(days=6),
        )
        TravelSignup.objects.create(
            organization=self.alpha,
            student=self.student,
            event=local_trip,
        )
        TravelParentChaperoneSignup.objects.create(
            organization=self.alpha,
            adult=self.parent,
            event=local_trip,
        )

        student_data = collect_student_digest_data(
            self.student, days_ahead=10, reference_date=self.today
        )
        parent_data = collect_parent_digest_data(
            self.parent, days_ahead=10, reference_date=self.today
        )

        self.assertNotIn(
            "Beta Confidential Outreach",
            {signup["event_name"] for signup in student_data["confirmed_shifts"]},
        )
        local_opportunity = next(
            opportunity
            for opportunity in student_data["open_opportunities"]
            if opportunity["event_name"] == "Local Outreach"
        )
        self.assertEqual(local_opportunity["open_champion_slots"], 1)
        self.assertEqual(student_data["outreach_summary"]["pending_hours"], 0)
        self.assertEqual(
            {trip["event_name"] for trip in student_data["travel_highlights"]},
            {"Local Trip"},
        )
        self.assertEqual(
            {trip["event_name"] for trip in parent_data["chaperone_signups"]},
            {"Local Trip"},
        )

    def test_required_documents_exclude_foreign_program_and_optional_documents(self):
        foreign_program = self._create_program(self.beta, "Beta Confidential Program")
        Enrollment.objects.create(
            organization=self.beta,
            student=self.student,
            program=foreign_program,
            active=True,
        )
        ProgramDocument.objects.create(
            program=self.local_program,
            name="Local Required Form",
            is_required=True,
            is_active=True,
        )
        local_document = ProgramDocument.objects.get(name="Local Required Form")
        foreign_student = Student.objects.create(
            organization=self.beta,
            legal_first_name="Foreign",
            last_name="Student",
        )
        StudentDocument.objects.create(
            student=foreign_student,
            program_document=local_document,
            file="foreign-student-submission.pdf",
        )
        ProgramDocument.objects.create(
            program=self.local_program,
            name="Local Optional Form",
            is_required=False,
            is_active=True,
        )
        ProgramDocument.objects.create(
            program=foreign_program,
            name="Beta Confidential Form",
            is_required=True,
            is_active=True,
        )

        data = collect_student_digest_data(
            self.student, days_ahead=7, reference_date=self.today
        )

        self.assertEqual(
            {document.name for document in data["action_items"]["missing_documents"]},
            {"Local Required Form"},
        )

    def test_unsafe_financial_statements_render_unavailable_in_student_and_parent_mail(
        self,
    ):
        self._enable_feature(self.local_program, "finances")
        foreign_program = self._create_program(self.beta, "Beta Confidential Program")
        self._enable_feature(foreign_program, "finances")
        Enrollment.objects.create(
            organization=self.beta,
            student=self.student,
            program=foreign_program,
            active=True,
        )
        Fee.objects.create(
            organization=self.alpha,
            program=self.local_program,
            name="Local dues",
            amount=Decimal("100.00"),
        )
        Fee.objects.create(
            organization=self.beta,
            program=self.local_program,
            name="Foreign-owned dues",
            amount=Decimal("25.00"),
        )
        Fee.objects.create(
            organization=self.beta,
            program=foreign_program,
            name="Beta dues",
            amount=Decimal("500.00"),
        )
        ProgramDocument.objects.create(
            program=foreign_program,
            name="Beta Confidential Form",
            is_required=True,
        )
        self._create_calendar_event("Local calendar event")

        student_data = collect_student_digest_data(
            self.student, days_ahead=7, reference_date=self.today
        )
        action_items = student_data["action_items"]
        self.assertTrue(action_items["balance_unavailable"])
        self.assertIsNone(action_items["total_balance_due"])
        self.assertEqual(action_items["program_balances"], [])

        mail.outbox.clear()
        call_command("send_weekly_digest", role="student", stdout=StringIO())
        self.assertEqual(len(mail.outbox), 1)
        student_body = mail.outbox[0].body
        self.assertIn("Balance unavailable", student_body)
        self.assertNotIn("$125.00", student_body)
        self.assertNotIn("$500.00", student_body)
        self.assertNotIn("Beta Confidential Program", student_body)
        self.assertNotIn("Beta Confidential Form", student_body)

        mail.outbox.clear()
        call_command("send_weekly_digest", role="parent", stdout=StringIO())
        self.assertEqual(len(mail.outbox), 1)
        parent_body = mail.outbox[0].body
        self.assertIn("Balance unavailable", parent_body)
        self.assertNotIn("$125.00", parent_body)
        self.assertNotIn("$500.00", parent_body)
        self.assertNotIn("Beta Confidential Program", parent_body)
        self.assertNotIn("Beta Confidential Form", parent_body)


class SendWeeklyDigestCommandTests(TestCase):
    def setUp(self):
        self.out = StringIO()
        self.today = timezone.localdate()
        self.program = Program.objects.create(
            name="Robotics Season 2026",
            active=True,
            start_date=self.today - datetime.timedelta(days=10),
            end_date=self.today + datetime.timedelta(days=90),
        )
        f_outreach, _ = ProgramFeature.objects.get_or_create(
            key="outreach", defaults={"name": "Outreach"}
        )
        f_attendance, _ = ProgramFeature.objects.get_or_create(
            key="attendance", defaults={"name": "Attendance"}
        )
        self.program.features.add(f_outreach, f_attendance)

        self.student_user = User.objects.create_user(
            username="student_user", email="student@example.com"
        )
        self.student = Student.objects.create(
            legal_first_name="Sarah",
            last_name="Connor",
            user=self.student_user,
            personal_email="student@example.com",
        )
        Enrollment.objects.create(
            student=self.student, program=self.program, active=True
        )

        self.parent_user = User.objects.create_user(
            username="parent_user", email="parent@example.com"
        )
        self.parent = Adult.objects.create(
            legal_first_name="Mary",
            last_name="Connor",
            is_parent=True,
            personal_email="parent@example.com",
            email_updates=True,
            user=self.parent_user,
        )
        AdultStudentRelationship.objects.create(
            adult=self.parent, student=self.student, relationship_to_student="parent"
        )
        self.student.primary_contact = self.parent
        self.student.save()

        self.mentor_user = User.objects.create_user(
            username="mentor_user", email="mentor@example.com"
        )
        self.mentor = Adult.objects.create(
            legal_first_name="Kyle",
            last_name="Reese",
            is_mentor=True,
            mentor_active=True,
            personal_email="mentor@example.com",
            user=self.mentor_user,
        )

        self.event = OutreachEvent.objects.create(
            program=self.program,
            name="Tech Expo",
            location_name="Convention Center",
            location_address="1000 Fort Duquesne Blvd",
        )
        self.shift = OutreachShift.objects.create(
            event=self.event,
            date=self.today + datetime.timedelta(days=3),
            start_time=datetime.time(9, 0),
            end_time=datetime.time(13, 0),
            max_champions=2,
            max_helpers=5,
        )
        OutreachSignup.objects.create(
            student=self.student, shift=self.shift, role=OutreachSignup.HELPER
        )

    def test_command_dry_run_does_not_send_email(self):
        mail.outbox.clear()
        with self.assertLogs(
            "programs.management.commands.send_weekly_digest", level="INFO"
        ) as cm:
            call_command("send_weekly_digest", dry_run=True, stdout=self.out)
        self.assertEqual(len(mail.outbox), 0)
        output = self.out.getvalue()
        logs = "\n".join(cm.output)
        self.assertIn("Dry run complete", output)
        self.assertIn("[DRY RUN]", logs)
        self.assertIn("Would send student weekly digest.", logs)
        self.assertNotIn("student@example.com", logs)
        self.assertNotIn("student@example.com", output)

    def test_command_sends_emails_to_active_users(self):
        mail.outbox.clear()
        with self.assertLogs(
            "programs.management.commands.send_weekly_digest", level="INFO"
        ) as cm:
            call_command("send_weekly_digest", stdout=self.out)
        # Should send to student, parent, mentor
        self.assertGreaterEqual(len(mail.outbox), 2)
        recipients = [m.to[0] for m in mail.outbox]
        self.assertIn("student@example.com", recipients)
        self.assertIn("parent@example.com", recipients)
        logs = "\n".join(cm.output)
        output = self.out.getvalue()
        self.assertIn("Sent student weekly digest.", logs)
        self.assertIn("Sent parent weekly digest.", logs)
        self.assertNotIn("student@example.com", logs)
        self.assertNotIn("student_user", logs)
        self.assertNotIn("parent@example.com", logs)
        self.assertNotIn("student@example.com", output)
        self.assertNotIn("parent@example.com", output)

    def test_command_filter_by_role(self):
        mail.outbox.clear()
        call_command("send_weekly_digest", role="student", stdout=self.out)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["student@example.com"])

    def test_command_filter_by_email(self):
        mail.outbox.clear()
        call_command("send_weekly_digest", email="parent@example.com", stdout=self.out)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["parent@example.com"])

    def test_command_email_subjects_include_date(self):
        mail.outbox.clear()
        update_organization_email_settings(
            self.student.organization,
            {"organization_name": "Digest Organization"},
        )
        call_command("send_weekly_digest", stdout=self.out)

        student_mail = next(m for m in mail.outbox if "student@example.com" in m.to)
        parent_mail = next(m for m in mail.outbox if "parent@example.com" in m.to)
        mentor_mail = next(m for m in mail.outbox if "mentor@example.com" in m.to)

        day = self.today.day
        if 10 <= (day % 100) <= 13:
            suffix = "th"
        else:
            suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
        expected_date = f"{self.today.strftime('%B')} {day}{suffix}"

        self.assertEqual(
            student_mail.subject,
            f"Your Digest Organization Portal Update for {expected_date}",
        )
        self.assertEqual(
            parent_mail.subject,
            f"Your Digest Organization Family Update for {expected_date}",
        )
        self.assertEqual(
            mentor_mail.subject,
            f"Your Digest Organization Mentor Update for {expected_date}",
        )
        self.assertIn("Digest Organization", student_mail.body)
        self.assertNotIn("portal.girlsofsteel.org", student_mail.body)

    def test_command_custom_date_option(self):
        mail.outbox.clear()
        self.shift.date = datetime.date(2026, 11, 3)
        self.shift.save(update_fields=["date"])
        call_command(
            "send_weekly_digest", date="2026-11-01", role="student", stdout=self.out
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(
            mail.outbox[0].subject,
            f"Your {get_organization_email_settings(self.student.organization)['organization_name']} Portal Update for November 1st",
        )

    def test_email_template_rendering_content(self):
        # Add attendance, calendar event, fee, document
        AttendanceSession.objects.create(
            program=self.program,
            student=self.student,
            check_in=timezone.now() - datetime.timedelta(days=1, hours=3),
            check_out=timezone.now() - datetime.timedelta(days=1),
            duration_minutes=180,
        )
        feed = CalendarFeed.objects.create(
            name="Robotics Feed",
            slug="robotics-feed",
            visibility=CalendarFeed.VISIBILITY_ANONYMOUS,
            source_type=CalendarFeed.SOURCE_MANUAL,
            is_active=True,
        )
        CalendarEvent.objects.create(
            feed=feed,
            title="Design Review Workshop",
            start_datetime=timezone.now() + datetime.timedelta(days=2),
            end_datetime=timezone.now() + datetime.timedelta(days=2, hours=2),
            location="Room 200",
        )
        f_finances, _ = ProgramFeature.objects.get_or_create(
            key="finances", defaults={"name": "Finances"}
        )
        self.program.features.add(f_finances)
        Fee.objects.create(
            program=self.program,
            name="Season Registration",
            amount=50.00,
        )

        mail.outbox.clear()
        call_command("send_weekly_digest", stdout=self.out)

        self.assertGreaterEqual(len(mail.outbox), 2)
        student_mail = next(m for m in mail.outbox if "student@example.com" in m.to)
        parent_mail = next(m for m in mail.outbox if "parent@example.com" in m.to)

        # Student email checks
        self.assertIn("Attendance Snapshot", student_mail.body)
        self.assertIn("Average Hours / Week:", student_mail.body)
        self.assertIn("Design Review Workshop", student_mail.body)
        self.assertIn("Program Balance Due", student_mail.body)

        # Parent email checks
        self.assertIn("Sarah Connor", parent_mail.body)
        self.assertIn("Attendance Snapshot", parent_mail.body)
        self.assertIn("Design Review Workshop", parent_mail.body)
        self.assertIn("Program Balance Due", parent_mail.body)
