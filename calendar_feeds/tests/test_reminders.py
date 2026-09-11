"""Tests for the send_calendar_reminders management command."""

from datetime import timedelta
from io import StringIO

from django.core import mail
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from calendar_feeds.models import CalendarFeed
from calendar_feeds.tests.factories import (
    make_event,
    make_feed,
    make_lead_mentor_user,
    make_student_user,
)


class SendCalendarRemindersCommandTests(TestCase):
    def setUp(self):
        self.out = StringIO()

    def test_no_events_no_emails(self):
        call_command("send_calendar_reminders", stdout=self.out)
        self.assertEqual(len(mail.outbox), 0)

    def test_upcoming_event_triggers_reminder(self):
        mail.outbox.clear()
        feed = make_feed(visibility=CalendarFeed.VISIBILITY_ANONYMOUS)
        lead = make_lead_mentor_user()
        lead.email = "lead@example.com"
        lead.save(update_fields=["email"])
        user, student = make_student_user()
        user.email = "student@example.com"
        user.save(update_fields=["email"])

        tomorrow = timezone.make_aware(
            timezone.datetime.now() + timedelta(days=1, hours=3)
        )
        make_event(
            feed,
            title="Build Night",
            start_datetime=tomorrow,
            end_datetime=tomorrow + timedelta(hours=2),
        )
        call_command("send_calendar_reminders", stdout=self.out)
        self.assertGreater(len(mail.outbox), 0)

    def test_future_event_no_reminder(self):
        mail.outbox.clear()
        feed = make_feed(visibility=CalendarFeed.VISIBILITY_ANONYMOUS)
        two_weeks = timezone.make_aware(timezone.datetime.now() + timedelta(days=14))
        make_event(
            feed,
            title="Far Future",
            start_datetime=two_weeks,
            end_datetime=two_weeks + timedelta(hours=1),
        )
        call_command("send_calendar_reminders", stdout=self.out)
        self.assertEqual(len(mail.outbox), 0)

    def test_dry_run_does_not_send(self):
        mail.outbox.clear()
        feed = make_feed(visibility=CalendarFeed.VISIBILITY_ANONYMOUS)
        tomorrow = timezone.make_aware(
            timezone.datetime.now() + timedelta(days=1, hours=2)
        )
        make_event(
            feed,
            title="Tomorrow",
            start_datetime=tomorrow,
            end_datetime=tomorrow + timedelta(hours=1),
        )
        call_command("send_calendar_reminders", dry_run=True, stdout=self.out)
        self.assertEqual(len(mail.outbox), 0)
