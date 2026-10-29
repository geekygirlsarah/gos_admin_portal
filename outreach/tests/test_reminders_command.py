"""Tests for the send_outreach_reminders management command."""

from datetime import date, time, timedelta
from io import StringIO

from django.contrib.auth.models import User
from django.core import mail
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from organizations.utils import get_organization_email_settings
from outreach.models import OutreachMentorSignup, OutreachShift, OutreachSignup
from outreach.tests.factories import create_outreach_event
from programs.models import Adult, Enrollment, Program, ProgramFeature, School, Student


class SendOutreachRemindersCommandTest(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Test School")
        self.feature, _ = ProgramFeature.objects.get_or_create(
            key="outreach", defaults={"name": "Outreach"}
        )
        self.program = Program.objects.create(name="GoS Robotics 2026")
        self.program.features.add(self.feature)

        # Student 1
        self.student1_user = User.objects.create_user(
            username="student1",
            email="student1@example.com",
            password="password",  # nosec B106
        )
        self.student1 = Student.objects.create(
            user=self.student1_user,
            legal_first_name="Jane",
            last_name="Doe",
            personal_email="student1@example.com",
            school=self.school,
            graduation_year=2027,
        )
        Enrollment.objects.create(
            student=self.student1, program=self.program, active=True
        )

        # Student 2
        self.student2_user = User.objects.create_user(
            username="student2",
            email="student2@example.com",
            password="password",  # nosec B106
        )
        self.student2 = Student.objects.create(
            user=self.student2_user,
            legal_first_name="Alice",
            last_name="Smith",
            personal_email="student2@example.com",
            school=self.school,
            graduation_year=2027,
        )
        Enrollment.objects.create(
            student=self.student2, program=self.program, active=True
        )

        # Mentor
        self.mentor_user = User.objects.create_user(
            username="mentor",
            email="mentor@example.com",
            password="password",  # nosec B106
        )
        self.mentor = Adult.objects.create(
            user=self.mentor_user,
            legal_first_name="Mentor",
            last_name="Bob",
            personal_email="mentor@example.com",
            is_mentor=True,
            mentor_active=True,
        )

        # Tomorrow's Event
        self.tomorrow = timezone.localdate() + timedelta(days=1)
        self.tomorrow_event = create_outreach_event(
            program=self.program,
            name="Library Science Fair",
            description="Hands on robot demonstration.",
            location_name="Carnegie Library",
            location_address="4400 Forbes Ave, Pittsburgh, PA",
            start_date=self.tomorrow,
            start_time=time(13, 0),
            end_time=time(16, 0),
        )
        self.tomorrow_shift = self.tomorrow_event.shifts.first()

        # Sign up student1 as champion, student2 as helper, and mentor
        OutreachSignup.objects.create(
            student=self.student1,
            shift=self.tomorrow_shift,
            role=OutreachSignup.CHAMPION,
        )
        OutreachSignup.objects.create(
            student=self.student2,
            shift=self.tomorrow_shift,
            role=OutreachSignup.HELPER,
        )
        OutreachMentorSignup.objects.create(
            adult=self.mentor,
            shift=self.tomorrow_shift,
        )

        # Event in 5 days (should NOT trigger reminders for tomorrow)
        self.future_date = timezone.localdate() + timedelta(days=5)
        self.future_event = create_outreach_event(
            program=self.program,
            name="Future Expo",
            start_date=self.future_date,
            start_time=time(9, 0),
            end_time=time(12, 0),
        )
        self.future_shift = self.future_event.shifts.first()
        OutreachSignup.objects.create(
            student=self.student1,
            shift=self.future_shift,
            role=OutreachSignup.HELPER,
        )

        mail.outbox.clear()

    def test_default_runs_for_tomorrow_events(self):
        out = StringIO()
        call_command("send_outreach_reminders", stdout=out)
        output = out.getvalue()

        # 3 emails should be sent: student1, student2, mentor
        self.assertEqual(len(mail.outbox), 3)
        recipients = {m.to[0] for m in mail.outbox}
        self.assertEqual(
            recipients,
            {"student1@example.com", "student2@example.com", "mentor@example.com"},
        )

        for email in mail.outbox:
            self.assertIn("Reminder", email.subject)
            self.assertIn("Library Science Fair", email.subject)
            self.assertIn("Carnegie Library", email.body)
            self.assertIn("4400 Forbes Ave", email.body)
            self.assertIn(
                "We included a calendar invite if you haven't added it to your calendar yet.",
                email.body,
            )
            self.assertIn("Log in to the Girls of Steel Portal", email.body)
            html_body, _ = email.alternatives[0]
            portal_url = (
                get_organization_email_settings().get("portal_url", "").rstrip("/")
            )
            expected_url = f"{portal_url}/programs/{self.program.id}/outreach/"
            self.assertIn(expected_url, html_body)
            self.assertIn("Jane Doe", email.body)
            self.assertIn("Alice Smith", email.body)
            self.assertIn("Mentor Bob", email.body)
            # Verify ICS attachment
            self.assertEqual(len(email.attachments), 1)
            filename, content, mimetype = email.attachments[0]
            self.assertTrue(filename.endswith(".ics"))
            self.assertEqual(mimetype, "text/calendar")
            self.assertIn("BEGIN:VCALENDAR", content)

    def test_dry_run_does_not_send_emails(self):
        out = StringIO()
        call_command("send_outreach_reminders", dry_run=True, stdout=out)
        output = out.getvalue()

        self.assertEqual(len(mail.outbox), 0)
        self.assertIn("DRY RUN", output)
        self.assertIn("Library Science Fair", output)

    def test_custom_days_ahead(self):
        out = StringIO()
        call_command("send_outreach_reminders", days_ahead=5, stdout=out)

        # Only future_event is in 5 days (student1 is helper)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["student1@example.com"])
        self.assertIn("Future Expo", mail.outbox[0].subject)

    def test_specific_date_argument(self):
        out = StringIO()
        call_command(
            "send_outreach_reminders",
            date=self.tomorrow.isoformat(),
            stdout=out,
        )
        self.assertEqual(len(mail.outbox), 3)

    def test_email_filter_argument(self):
        out = StringIO()
        call_command(
            "send_outreach_reminders",
            email="student1@example.com",
            stdout=out,
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["student1@example.com"])

    def test_no_events_found(self):
        # Move tomorrow shift to past
        self.tomorrow_shift.date = timezone.localdate() - timedelta(days=2)
        self.tomorrow_shift.save()

        out = StringIO()
        call_command("send_outreach_reminders", stdout=out)
        self.assertEqual(len(mail.outbox), 0)
        self.assertIn("No outreach shifts found", out.getvalue())
