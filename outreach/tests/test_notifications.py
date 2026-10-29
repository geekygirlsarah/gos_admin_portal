"""Tests for outreach signup confirmation emails and ICS generation."""

from datetime import date, time, timedelta

from django.contrib.auth.models import Group, User
from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from organizations.utils import get_organization_email_settings
from outreach.models import (
    OutreachEvent,
    OutreachMentorSignup,
    OutreachShift,
    OutreachSignup,
)
from outreach.services import (
    generate_shift_ics,
    notify_mentor_outreach_signup,
    notify_outreach_event_deleted,
    notify_outreach_signup,
)
from outreach.tests.factories import create_outreach_event
from programs.models import Adult, Enrollment, Program, ProgramFeature, School, Student


class OutreachNotificationsTest(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Test High School")
        self.feature, _ = ProgramFeature.objects.get_or_create(
            key="outreach", defaults={"name": "Outreach"}
        )
        self.program = Program.objects.create(name="GoS Robotics 2026")
        self.program.features.add(self.feature)

        # Student 1 with personal email
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
        self.mentor_adult = Adult.objects.create(
            user=self.mentor_user,
            legal_first_name="Mentor",
            last_name="Bob",
            personal_email="mentor@example.com",
            is_mentor=True,
            mentor_active=True,
        )

        # Lead Mentor
        self.lead_mentor_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.lead_user = User.objects.create_user(
            username="lead", email="lead@example.com", password="password"  # nosec B106
        )
        self.lead_user.groups.add(self.lead_mentor_group)
        self.lead_adult = Adult.objects.create(
            user=self.lead_user,
            legal_first_name="Lead",
            last_name="Mentor",
            personal_email="lead@example.com",
            is_mentor=True,
            mentor_active=True,
        )

        # Event with shift
        self.shift_date = timezone.localdate() + timedelta(days=5)
        self.event = create_outreach_event(
            program=self.program,
            name="Robotics Workshop",
            description="Teaching kids robotics basics.",
            location_name="Carnegie Mellon University",
            location_address="5000 Forbes Ave, Pittsburgh, PA",
            start_date=self.shift_date,
            start_time=time(10, 0),
            end_time=time(13, 0),
        )
        self.shift = self.event.shifts.first()
        self.shift.max_champions = 2
        self.shift.max_helpers = 5
        self.shift.save()

    def test_generate_shift_ics(self):
        ics_text = generate_shift_ics(self.shift, role_label="Champion")
        self.assertIn("BEGIN:VCALENDAR", ics_text)
        self.assertIn("VERSION:2.0", ics_text)
        self.assertIn("BEGIN:VEVENT", ics_text)
        self.assertIn(f"SUMMARY:Robotics Workshop (Champion)", ics_text)
        self.assertIn("Teaching kids robotics basics", ics_text)
        self.assertIn("Carnegie Mellon University", ics_text)
        self.assertIn("END:VEVENT", ics_text)
        self.assertIn("END:VCALENDAR", ics_text)

    def test_student_direct_signup_sends_email_with_ics(self):
        # Pre-sign a helper and a mentor to test participant listing in the email
        OutreachSignup.objects.create(
            student=self.student2, shift=self.shift, role=OutreachSignup.HELPER
        )
        OutreachMentorSignup.objects.create(adult=self.mentor_adult, shift=self.shift)

        self.client.login(username="student1", password="password")  # nosec B106
        url = reverse("outreach:shift_signup", args=[self.program.id, self.shift.pk])
        resp = self.client.post(url, {"role": OutreachSignup.CHAMPION})
        self.assertEqual(resp.status_code, 302)

        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertEqual(email.to, ["student1@example.com"])
        self.assertIn("Robotics Workshop", email.subject)
        self.assertIn("Champion", email.subject)
        self.assertIn("Jane Doe", email.body)
        self.assertIn("Carnegie Mellon University", email.body)
        self.assertIn("5000 Forbes Ave", email.body)

        # Verify kid-friendly calendar invite wording and portal login link
        self.assertIn(
            "We included a calendar invite if you haven't added it to your calendar yet.",
            email.body,
        )
        self.assertIn("Log in to the Girls of Steel Portal", email.body)
        self.assertNotIn("A calendar event (.ics)", email.body)
        html_body, _ = email.alternatives[0]
        portal_url = get_organization_email_settings().get("portal_url", "").rstrip("/")
        expected_url = f"{portal_url}/programs/{self.program.id}/outreach/"
        self.assertIn(expected_url, html_body)

        # Verify other participants are listed in the email
        self.assertIn("Alice Smith", email.body)
        self.assertIn("Mentor Bob", email.body)

        # Verify ICS attachment
        self.assertEqual(len(email.attachments), 1)
        filename, content, mimetype = email.attachments[0]
        self.assertTrue(filename.endswith(".ics"))
        self.assertEqual(mimetype, "text/calendar")
        self.assertIn("BEGIN:VCALENDAR", content)
        self.assertIn("Robotics Workshop", content)

    def test_student_helper_signup_sends_email_with_ics(self):
        self.client.login(username="student2", password="password")  # nosec B106
        url = reverse("outreach:shift_signup", args=[self.program.id, self.shift.pk])
        resp = self.client.post(url, {"role": OutreachSignup.HELPER})
        self.assertEqual(resp.status_code, 302)

        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertEqual(email.to, ["student2@example.com"])
        self.assertIn("Helper", email.subject)

    def test_student_creates_event_auto_champion_sends_email(self):
        self.client.login(username="student1", password="password")  # nosec B106
        url = reverse("outreach:event_create", args=[self.program.id])
        post_data = {
            "name": "STEM Demo Day",
            "description": "Fun science demos",
            "location_name": "Library",
            "location_address": "100 Main St",
            "shifts-TOTAL_FORMS": "1",
            "shifts-INITIAL_FORMS": "0",
            "shifts-MIN_NUM_FORMS": "1",
            "shifts-MAX_NUM_FORMS": "1000",
            "shifts-0-date": (timezone.localdate() + timedelta(days=10)).isoformat(),
            "shifts-0-start_time": "14:00",
            "shifts-0-end_time": "16:00",
            "shifts-0-max_champions": "2",
            "shifts-0-max_helpers": "4",
        }
        resp = self.client.post(url, post_data)
        self.assertEqual(resp.status_code, 302)

        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertEqual(email.to, ["student1@example.com"])
        self.assertIn("STEM Demo Day", email.subject)
        self.assertIn("Champion", email.subject)

    def test_manage_signups_sends_emails_to_new_students_only(self):
        # Pre-sign student1
        OutreachSignup.objects.create(
            student=self.student1, shift=self.shift, role=OutreachSignup.CHAMPION
        )
        mail.outbox.clear()

        # Mentor adds student2 as helper, keeping student1 as champion
        self.client.login(username="mentor", password="password")  # nosec B106
        url = reverse(
            "outreach:shift_manage_signups", args=[self.program.id, self.shift.pk]
        )
        resp = self.client.post(
            url,
            {"champions": [self.student1.pk], "helpers": [self.student2.pk]},
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)

        # Only student2 should receive a signup notification
        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertEqual(email.to, ["student2@example.com"])
        self.assertIn("Helper", email.subject)

    def test_mentor_direct_signup_sends_email_with_ics(self):
        # Pre-sign a student champion
        OutreachSignup.objects.create(
            student=self.student1, shift=self.shift, role=OutreachSignup.CHAMPION
        )

        self.client.login(username="mentor", password="password")  # nosec B106
        url = reverse(
            "outreach:shift_mentor_signup", args=[self.program.id, self.shift.pk]
        )
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 302)

        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertEqual(email.to, ["mentor@example.com"])
        self.assertIn("Robotics Workshop", email.subject)
        self.assertIn("Mentor", email.subject)
        self.assertIn("Jane Doe", email.body)
        self.assertIn("Mentor Bob", email.body)
        self.assertIn(
            "We included a calendar invite if you haven't added it to your calendar yet.",
            email.body,
        )
        self.assertIn("Log in to the Girls of Steel Portal", email.body)
        html_body, _ = email.alternatives[0]
        portal_url = get_organization_email_settings().get("portal_url", "").rstrip("/")
        expected_url = f"{portal_url}/programs/{self.program.id}/outreach/"
        self.assertIn(expected_url, html_body)

        # Verify ICS attachment
        self.assertEqual(len(email.attachments), 1)
        filename, content, mimetype = email.attachments[0]
        self.assertTrue(filename.endswith(".ics"))
        self.assertEqual(mimetype, "text/calendar")

    def test_lead_mentor_manage_mentor_signups_sends_email_to_new_mentors(self):
        self.client.login(username="lead", password="password")  # nosec B106
        url = reverse(
            "outreach:shift_manage_mentor_signups",
            args=[self.program.id, self.shift.pk],
        )
        resp = self.client.post(
            url,
            {"mentors": [self.mentor_adult.pk]},
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)

        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertEqual(email.to, ["mentor@example.com"])
        self.assertIn("Mentor", email.subject)

    def test_notification_with_custom_organization_domain(self):
        from organizations.models import Organization, OrganizationSettings

        org = Organization.objects.create(name="Custom Org", slug="custom")
        org_settings = OrganizationSettings.objects.create(
            organization=org,
            data={
                "email_settings": {
                    "organization_name": "Custom Robotics Org",
                    "portal_url": "https://robotics.example.com",
                }
            },
        )
        self.program.organization = org
        self.program.save()
        self.shift.event.organization = org
        self.shift.event.save()

        self.client.login(username="student1", password="password")  # nosec B106
        url = reverse("outreach:shift_signup", args=[self.program.id, self.shift.pk])
        resp = self.client.post(url, {"role": OutreachSignup.CHAMPION})
        self.assertEqual(resp.status_code, 302)

        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertIn("Log in to the Custom Robotics Org Portal", email.body)
        html_body, _ = email.alternatives[0]
        expected_url = (
            f"https://robotics.example.com/programs/{self.program.id}/outreach/"
        )
        self.assertIn(expected_url, html_body)
        self.assertIn(
            'Log in to the <a href="https://robotics.example.com/programs/', html_body
        )

    def test_event_deletion_notifies_champions_helpers_and_mentors(self):
        # Setup champion, helper, and mentor
        OutreachSignup.objects.create(
            student=self.student1, shift=self.shift, role=OutreachSignup.CHAMPION
        )
        OutreachSignup.objects.create(
            student=self.student2, shift=self.shift, role=OutreachSignup.HELPER
        )
        OutreachMentorSignup.objects.create(adult=self.mentor_adult, shift=self.shift)

        mail.outbox.clear()

        self.client.login(username="lead", password="password")  # nosec B106
        url = reverse("outreach:event_delete", args=[self.program.id, self.event.pk])
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(OutreachEvent.objects.filter(pk=self.event.pk).exists())

        # Should send 3 notification emails
        self.assertEqual(len(mail.outbox), 3)

        recipients = {email.to[0] for email in mail.outbox}
        self.assertEqual(
            recipients,
            {"student1@example.com", "student2@example.com", "mentor@example.com"},
        )

        for email in mail.outbox:
            self.assertIn("Cancelled", email.subject)
            self.assertIn("Robotics Workshop", email.subject)
            self.assertIn("Robotics Workshop", email.body)
            self.assertIn("Carnegie Mellon University", email.body)
            self.assertIn("Log in to the Girls of Steel Portal", email.body)

        # Verify role and specific greetings in each email
        student1_mail = next(e for e in mail.outbox if e.to == ["student1@example.com"])
        self.assertIn("Jane Doe", student1_mail.body)
        self.assertIn("Champion", student1_mail.body)

        student2_mail = next(e for e in mail.outbox if e.to == ["student2@example.com"])
        self.assertIn("Alice Smith", student2_mail.body)
        self.assertIn("Helper", student2_mail.body)

        mentor_mail = next(e for e in mail.outbox if e.to == ["mentor@example.com"])
        self.assertIn("Mentor Bob", mentor_mail.body)
        self.assertIn("Mentor Support", mentor_mail.body)

    def test_event_deletion_with_no_signups_sends_no_emails(self):
        mail.outbox.clear()

        self.client.login(username="lead", password="password")  # nosec B106
        url = reverse("outreach:event_delete", args=[self.program.id, self.event.pk])
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(OutreachEvent.objects.filter(pk=self.event.pk).exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_event_deletion_deduplicates_multi_shift_signups(self):
        # Create second shift on the event
        shift2 = OutreachShift.objects.create(
            event=self.event,
            date=self.shift_date,
            start_time=time(14, 0),
            end_time=time(17, 0),
            max_champions=2,
            max_helpers=5,
        )
        # Student1 signed up on shift 1 and shift 2
        OutreachSignup.objects.create(
            student=self.student1, shift=self.shift, role=OutreachSignup.CHAMPION
        )
        OutreachSignup.objects.create(
            student=self.student1, shift=shift2, role=OutreachSignup.HELPER
        )
        # Mentor signed up on shift 1 and shift 2
        OutreachMentorSignup.objects.create(adult=self.mentor_adult, shift=self.shift)
        OutreachMentorSignup.objects.create(adult=self.mentor_adult, shift=shift2)

        mail.outbox.clear()

        self.client.login(username="lead", password="password")  # nosec B106
        url = reverse("outreach:event_delete", args=[self.program.id, self.event.pk])
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 302)

        # Exactly 2 emails: 1 for student1, 1 for mentor_adult
        self.assertEqual(len(mail.outbox), 2)
        recipients = {email.to[0] for email in mail.outbox}
        self.assertEqual(recipients, {"student1@example.com", "mentor@example.com"})
