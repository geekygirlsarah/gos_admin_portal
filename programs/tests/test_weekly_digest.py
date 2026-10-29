"""Tests for the weekly automated digest service and management command."""

import datetime
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core import mail
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from attendance.models import AttendanceSession
from calendar_feeds.models import CalendarEvent, CalendarFeed
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
    get_weekly_digest_recipients,
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
        call_command("send_weekly_digest", dry_run=True, stdout=self.out)
        self.assertEqual(len(mail.outbox), 0)
        output = self.out.getvalue()
        self.assertIn("Dry run complete", output)

    def test_command_sends_emails_to_active_users(self):
        mail.outbox.clear()
        call_command("send_weekly_digest", stdout=self.out)
        # Should send to student, parent, mentor
        self.assertGreaterEqual(len(mail.outbox), 2)
        recipients = [m.to[0] for m in mail.outbox]
        self.assertIn("student@example.com", recipients)
        self.assertIn("parent@example.com", recipients)

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
            f"Your Girls of Steel Portal Update for {expected_date}",
        )
        self.assertEqual(
            parent_mail.subject,
            f"Your Girls of Steel Family Update for {expected_date}",
        )
        self.assertEqual(
            mentor_mail.subject,
            f"Your Girls of Steel Mentor Update for {expected_date}",
        )

    def test_command_custom_date_option(self):
        mail.outbox.clear()
        call_command(
            "send_weekly_digest", date="2026-11-01", role="student", stdout=self.out
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(
            mail.outbox[0].subject,
            "Your Girls of Steel Portal Update for November 1st",
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
