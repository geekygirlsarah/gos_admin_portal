"""Tests for the send_outreach_reminders management command."""

from datetime import time, timedelta
from io import StringIO

from django.contrib.auth.models import User
from django.core import mail
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from organizations.models import Organization
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
        with self.assertLogs(
            "outreach.management.commands.send_outreach_reminders", level="INFO"
        ) as cm:
            call_command("send_outreach_reminders", stdout=out)
        logs = "\n".join(cm.output)

        self.assertIn("Sent an outreach student reminder.", logs)
        self.assertIn("Sent an outreach mentor reminder.", logs)
        for sensitive_value in (
            "student1@example.com",
            "student2@example.com",
            "mentor@example.com",
            "Library Science Fair",
            "Jane Doe",
            "Alice Smith",
            "Mentor Bob",
        ):
            self.assertNotIn(sensitive_value, logs)

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

    def test_multiple_shifts_for_same_person_and_event_are_combined(self):
        second_shift = OutreachShift.objects.create(
            organization=self.tomorrow_shift.organization,
            event=self.tomorrow_event,
            date=self.tomorrow,
            start_time=time(16, 0),
            end_time=time(18, 0),
        )
        OutreachSignup.objects.create(
            student=self.student1,
            shift=second_shift,
            role=OutreachSignup.HELPER,
        )
        OutreachMentorSignup.objects.create(adult=self.mentor, shift=second_shift)

        other_event = create_outreach_event(
            program=self.program,
            name="Second Library Event",
            start_date=self.tomorrow,
            start_time=time(9, 0),
            end_time=time(10, 0),
        )
        OutreachSignup.objects.create(
            student=self.student1,
            shift=other_event.shifts.first(),
            role=OutreachSignup.CHAMPION,
        )

        call_command(
            "send_outreach_reminders",
            date=self.tomorrow.isoformat(),
            stdout=StringIO(),
        )

        student1_emails = [
            message for message in mail.outbox if message.to == ["student1@example.com"]
        ]
        mentor_emails = [
            message for message in mail.outbox if message.to == ["mentor@example.com"]
        ]
        self.assertEqual(len(student1_emails), 2)
        self.assertEqual(len(mentor_emails), 1)

        combined_student_email = next(
            message
            for message in student1_emails
            if "Library Science Fair" in message.subject
        )
        self.assertIn("1:00 PM", combined_student_email.body)
        self.assertIn("4:00 PM", combined_student_email.body)
        self.assertIn("Champion", combined_student_email.body)
        self.assertIn("Helper", combined_student_email.body)
        self.assertIn("Alice Smith", combined_student_email.body)
        self.assertIn("Jane Doe", combined_student_email.body)
        self.assertEqual(len(combined_student_email.attachments), 2)
        filenames = [attachment[0] for attachment in combined_student_email.attachments]
        self.assertEqual(len(filenames), len(set(filenames)))
        for filename, content, mimetype in combined_student_email.attachments:
            self.assertTrue(filename.endswith(".ics"))
            self.assertEqual(mimetype, "text/calendar")
            self.assertIn("BEGIN:VCALENDAR", content)
        self.assertTrue(
            any("Second Library Event" in email.subject for email in student1_emails)
        )

        combined_mentor_email = mentor_emails[0]
        self.assertIn("1:00 PM", combined_mentor_email.body)
        self.assertIn("4:00 PM", combined_mentor_email.body)
        self.assertIn("Mentor Support", combined_mentor_email.body)
        self.assertIn("Jane Doe", combined_mentor_email.body)
        self.assertIn("Alice Smith", combined_mentor_email.body)
        self.assertEqual(len(combined_mentor_email.attachments), 2)

    def test_grouped_reminders_respect_dry_run_and_email_filter(self):
        second_shift = OutreachShift.objects.create(
            organization=self.tomorrow_shift.organization,
            event=self.tomorrow_event,
            date=self.tomorrow,
            start_time=time(16, 0),
            end_time=time(18, 0),
        )
        OutreachSignup.objects.create(
            student=self.student1,
            shift=second_shift,
            role=OutreachSignup.HELPER,
        )

        dry_run_output = StringIO()
        call_command(
            "send_outreach_reminders",
            date=self.tomorrow.isoformat(),
            dry_run=True,
            stdout=dry_run_output,
        )
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(
            dry_run_output.getvalue().count("Would send a student reminder."), 2
        )
        self.assertEqual(
            dry_run_output.getvalue().count("Would send a mentor reminder."), 1
        )

        call_command(
            "send_outreach_reminders",
            date=self.tomorrow.isoformat(),
            email="STUDENT1@example.com",
            stdout=StringIO(),
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["student1@example.com"])
        self.assertEqual(len(mail.outbox[0].attachments), 2)

    def test_dry_run_does_not_send_emails(self):
        out = StringIO()
        with self.assertLogs(
            "outreach.management.commands.send_outreach_reminders", level="INFO"
        ) as cm:
            call_command("send_outreach_reminders", dry_run=True, stdout=out)
        output = out.getvalue()
        logs = "\n".join(cm.output)

        self.assertEqual(len(mail.outbox), 0)
        self.assertIn("DRY RUN", output)
        self.assertIn("Would send a student reminder.", output)
        self.assertIn("Would send a mentor reminder.", output)
        self.assertIn("[DRY RUN]", logs)
        for sensitive_value in (
            "student1@example.com",
            "student2@example.com",
            "mentor@example.com",
            "Library Science Fair",
            "Jane Doe",
            "Alice Smith",
            "Mentor Bob",
        ):
            self.assertNotIn(sensitive_value, output)
            self.assertNotIn(sensitive_value, logs)

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

    def test_reminders_skip_foreign_owned_signups_on_local_shift(self):
        foreign_org = Organization.objects.create(
            name="Reminder Beta", slug="reminder-beta", subdomain="reminder-beta"
        )
        foreign_student = Student.objects.create(
            organization=foreign_org,
            legal_first_name="Foreign",
            last_name="Student",
            personal_email="foreign-student@example.com",
        )
        foreign_mentor = Adult.objects.create(
            organization=foreign_org,
            legal_first_name="Foreign",
            last_name="Mentor",
            personal_email="foreign-mentor@example.com",
            is_mentor=True,
            mentor_active=True,
        )
        OutreachSignup.objects.bulk_create(
            [
                OutreachSignup(
                    organization=foreign_org,
                    student=foreign_student,
                    shift=self.tomorrow_shift,
                    role=OutreachSignup.HELPER,
                )
            ]
        )
        OutreachMentorSignup.objects.bulk_create(
            [
                OutreachMentorSignup(
                    organization=foreign_org,
                    adult=foreign_mentor,
                    shift=self.tomorrow_shift,
                )
            ]
        )
        mail.outbox.clear()

        call_command(
            "send_outreach_reminders",
            date=self.tomorrow.isoformat(),
            stdout=StringIO(),
        )

        recipients = {message.to[0] for message in mail.outbox}
        self.assertEqual(
            recipients,
            {"student1@example.com", "student2@example.com", "mentor@example.com"},
        )
        for message in mail.outbox:
            self.assertNotIn("Foreign Student", message.body)
            self.assertNotIn("Foreign Mentor", message.body)

    def test_reminders_skip_inactive_organizations(self):
        organization = self.tomorrow_shift.organization
        organization.is_active = False
        organization.save(update_fields=["is_active"])

        call_command(
            "send_outreach_reminders",
            date=self.tomorrow.isoformat(),
            stdout=StringIO(),
        )

        self.assertEqual(mail.outbox, [])

    def test_reminders_skip_inactive_students_and_mentors(self):
        Enrollment.objects.filter(student=self.student1, program=self.program).update(
            active=False
        )
        self.mentor.mentor_active = False
        self.mentor.save(update_fields=["mentor_active"])

        call_command(
            "send_outreach_reminders",
            date=self.tomorrow.isoformat(),
            stdout=StringIO(),
        )

        self.assertEqual(
            {message.to[0] for message in mail.outbox}, {"student2@example.com"}
        )
