"""Tests for CalendarEventForm recurrence builder."""

from datetime import date, datetime

from django.test import TestCase
from django.utils import timezone

from calendar_feeds.forms import CalendarEventForm
from calendar_feeds.models import CalendarEvent
from calendar_feeds.tests.factories import make_feed


class CalendarEventFormRecurrenceTests(TestCase):
    def setUp(self):
        self.feed = make_feed()
        self.now = timezone.make_aware(datetime(2026, 10, 1, 18, 0), timezone.UTC)
        self.end = timezone.make_aware(datetime(2026, 10, 1, 20, 0), timezone.UTC)

    def test_no_repeat_clears_rrule(self):
        form_data = {
            "title": "One-Time Meeting",
            "start_datetime": "2026-10-01T18:00",
            "end_datetime": "2026-10-01T20:00",
            "recurrence_type": "none",
        }
        form = CalendarEventForm(data=form_data)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["rrule_ical"], "")

    def test_daily_repeat_with_count(self):
        form_data = {
            "title": "Daily Standup",
            "start_datetime": "2026-10-01T18:00",
            "end_datetime": "2026-10-01T18:30",
            "recurrence_type": "daily",
            "repeat_interval": 1,
            "repeat_end_type": "count",
            "repeat_count": 10,
        }
        form = CalendarEventForm(data=form_data)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["rrule_ical"], "FREQ=DAILY;COUNT=10")

    def test_weekly_repeat_with_days_and_until(self):
        form_data = {
            "title": "Subteam Build",
            "start_datetime": "2026-10-01T18:00",
            "end_datetime": "2026-10-01T20:00",
            "recurrence_type": "weekly",
            "repeat_interval": 1,
            "repeat_days": ["TU", "TH"],
            "repeat_end_type": "until",
            "repeat_until": "2026-12-15",
        }
        form = CalendarEventForm(data=form_data)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            form.cleaned_data["rrule_ical"],
            "FREQ=WEEKLY;BYDAY=TU,TH;UNTIL=20261215T235959Z",
        )

    def test_weekly_repeat_with_interval(self):
        form_data = {
            "title": "Bi-Weekly Planning",
            "start_datetime": "2026-10-01T18:00",
            "end_datetime": "2026-10-01T20:00",
            "recurrence_type": "weekly",
            "repeat_interval": 2,
            "repeat_days": ["MO"],
            "repeat_end_type": "never",
        }
        form = CalendarEventForm(data=form_data)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            form.cleaned_data["rrule_ical"], "FREQ=WEEKLY;INTERVAL=2;BYDAY=MO"
        )

    def test_monthly_repeat(self):
        form_data = {
            "title": "Monthly All-Hands",
            "start_datetime": "2026-10-01T18:00",
            "end_datetime": "2026-10-01T19:00",
            "recurrence_type": "monthly",
            "repeat_interval": 1,
            "repeat_end_type": "never",
        }
        form = CalendarEventForm(data=form_data)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["rrule_ical"], "FREQ=MONTHLY")

    def test_form_initial_parses_existing_rrule(self):
        event = CalendarEvent.objects.create(
            feed=self.feed,
            title="Weekly Team Meeting",
            start_datetime=self.now,
            end_datetime=self.end,
            rrule_ical="FREQ=WEEKLY;INTERVAL=2;BYDAY=TU,TH;COUNT=15",
        )
        form = CalendarEventForm(instance=event)
        self.assertEqual(form.initial.get("recurrence_type"), "weekly")
        self.assertEqual(form.initial.get("repeat_interval"), 2)
        self.assertEqual(form.initial.get("repeat_days"), ["TU", "TH"])
        self.assertEqual(form.initial.get("repeat_end_type"), "count")
        self.assertEqual(form.initial.get("repeat_count"), 15)
