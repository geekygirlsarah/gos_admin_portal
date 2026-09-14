"""Tests for recurring event expansion and exception handling."""

from datetime import date, timedelta

from django.test import TestCase
from django.utils import timezone

from calendar_feeds.models import CalendarEvent, EventException
from calendar_feeds.tests.factories import make_event, make_feed
from calendar_feeds.utils import expand_events_for_range


def utc_dt(*args):
    return timezone.make_aware(timezone.datetime(*args), timezone.UTC)


class WeeklyRecurrenceTests(TestCase):
    def setUp(self):
        self.feed = make_feed()
        # Start on a known Tuesday
        self.start = utc_dt(2026, 9, 1, 10, 0)
        self.event = CalendarEvent.objects.create(
            feed=self.feed,
            title="Team Standup",
            start_datetime=self.start,
            end_datetime=self.start + timedelta(hours=1),
            rrule_ical="FREQ=WEEKLY;BYDAY=TU;COUNT=4",
        )

    def test_expands_four_instances(self):
        instances = expand_events_for_range(
            self.feed, date(2026, 9, 1), date(2026, 9, 30)
        )
        self.assertEqual(len(instances), 4)

    def test_dates_are_all_tuesdays(self):
        instances = expand_events_for_range(
            self.feed, date(2026, 9, 1), date(2026, 9, 30)
        )
        dates = [i["date"] for i in instances]
        for d in dates:
            self.assertEqual(d.weekday(), 1)  # Tuesday

    def test_outside_range_returns_none(self):
        instances = expand_events_for_range(
            self.feed, date(2026, 12, 1), date(2026, 12, 31)
        )
        self.assertEqual(len(instances), 0)


class DailyRecurrenceTests(TestCase):
    def setUp(self):
        self.feed = make_feed()
        self.start = utc_dt(2026, 9, 1, 8, 0)
        self.event = CalendarEvent.objects.create(
            feed=self.feed,
            title="Morning Huddle",
            start_datetime=self.start,
            end_datetime=self.start + timedelta(minutes=30),
            rrule_ical="FREQ=DAILY;COUNT=5",
        )

    def test_daily_five_days(self):
        instances = expand_events_for_range(
            self.feed, date(2026, 9, 1), date(2026, 9, 5)
        )
        self.assertEqual(len(instances), 5)


class ExceptionDeletedTests(TestCase):
    def setUp(self):
        self.feed = make_feed()
        self.start = utc_dt(2026, 9, 1, 10, 0)
        self.event = CalendarEvent.objects.create(
            feed=self.feed,
            title="Weekly",
            start_datetime=self.start,
            end_datetime=self.start + timedelta(hours=1),
            rrule_ical="FREQ=WEEKLY;BYDAY=TU;COUNT=4",
        )
        # Delete the Sept 8 occurrence
        EventException.objects.create(
            event=self.event,
            original_date=date(2026, 9, 8),
            change_type=EventException.CHANGE_DELETED,
        )

    def test_deleted_date_skipped(self):
        instances = expand_events_for_range(
            self.feed, date(2026, 9, 1), date(2026, 9, 30)
        )
        dates = [i["date"] for i in instances]
        self.assertNotIn(date(2026, 9, 8), dates)
        self.assertEqual(len(instances), 3)


class ExceptionMovedTests(TestCase):
    def setUp(self):
        self.feed = make_feed()
        self.start = utc_dt(2026, 9, 1, 10, 0)
        self.event = CalendarEvent.objects.create(
            feed=self.feed,
            title="Weekly",
            start_datetime=self.start,
            end_datetime=self.start + timedelta(hours=1),
            rrule_ical="FREQ=WEEKLY;BYDAY=TU;COUNT=4",
        )
        new_start = utc_dt(2026, 9, 9, 14, 0)
        EventException.objects.create(
            event=self.event,
            original_date=date(2026, 9, 8),
            change_type=EventException.CHANGE_MOVED,
            new_start_datetime=new_start,
            new_end_datetime=new_start + timedelta(hours=1),
        )

    def test_original_date_skipped(self):
        instances = expand_events_for_range(
            self.feed, date(2026, 9, 1), date(2026, 9, 30)
        )
        dates = [i["date"] for i in instances]
        self.assertNotIn(date(2026, 9, 8), dates)
        self.assertIn(date(2026, 9, 9), dates)

    def test_moved_instance_uses_new_time(self):
        instances = expand_events_for_range(
            self.feed, date(2026, 9, 1), date(2026, 9, 30)
        )
        moved = [i for i in instances if i["date"] == date(2026, 9, 9)]
        self.assertEqual(len(moved), 1)
        self.assertEqual(moved[0]["start_datetime"].hour, 14)


class ExceptionChangedTests(TestCase):
    def setUp(self):
        self.feed = make_feed()
        self.start = utc_dt(2026, 9, 1, 10, 0)
        self.event = CalendarEvent.objects.create(
            feed=self.feed,
            title="Weekly",
            start_datetime=self.start,
            end_datetime=self.start + timedelta(hours=1),
            rrule_ical="FREQ=WEEKLY;BYDAY=TU;COUNT=4",
        )
        EventException.objects.create(
            event=self.event,
            original_date=date(2026, 9, 8),
            change_type=EventException.CHANGE_CHANGED,
            new_title="Special Session",
            new_location="Room 200",
        )

    def test_changed_date_preserved(self):
        instances = expand_events_for_range(
            self.feed, date(2026, 9, 1), date(2026, 9, 30)
        )
        changed = [i for i in instances if i["date"] == date(2026, 9, 8)]
        self.assertEqual(len(changed), 1)
        self.assertEqual(changed[0]["title"], "Special Session")
        self.assertEqual(changed[0]["location"], "Room 200")


class ExdateTests(TestCase):
    def setUp(self):
        self.feed = make_feed()
        self.start = utc_dt(2026, 9, 1, 10, 0)
        self.event = CalendarEvent.objects.create(
            feed=self.feed,
            title="Weekly",
            start_datetime=self.start,
            end_datetime=self.start + timedelta(hours=1),
            rrule_ical="FREQ=WEEKLY;BYDAY=TU;COUNT=4",
            exdate_ical="2026-09-08",
        )

    def test_exdate_skips_date(self):
        instances = expand_events_for_range(
            self.feed, date(2026, 9, 1), date(2026, 9, 30)
        )
        dates = [i["date"] for i in instances]
        self.assertNotIn(date(2026, 9, 8), dates)
        self.assertEqual(len(instances), 3)


class OneOffEventTests(TestCase):
    def test_one_off_within_range(self):
        feed = make_feed()
        start = utc_dt(2026, 9, 5, 10, 0)
        make_event(feed, start_datetime=start, end_datetime=start + timedelta(hours=1))
        instances = expand_events_for_range(feed, date(2026, 9, 1), date(2026, 9, 10))
        self.assertEqual(len(instances), 1)

    def test_one_off_outside_range(self):
        feed = make_feed()
        start = utc_dt(2026, 11, 5, 10, 0)
        make_event(feed, start_datetime=start, end_datetime=start + timedelta(hours=1))
        instances = expand_events_for_range(feed, date(2026, 9, 1), date(2026, 9, 10))
        self.assertEqual(len(instances), 0)


class AllDayEventTests(TestCase):
    def test_all_day_recurring(self):
        feed = make_feed()
        start = utc_dt(2026, 9, 1, 0, 0)
        CalendarEvent.objects.create(
            feed=feed,
            title="All Day Event",
            start_datetime=start,
            end_datetime=start + timedelta(days=1),
            all_day=True,
            rrule_ical="FREQ=WEEKLY;BYDAY=TU;COUNT=2",
        )
        instances = expand_events_for_range(feed, date(2026, 9, 1), date(2026, 9, 15))
        self.assertEqual(len(instances), 2)
        for inst in instances:
            self.assertTrue(inst["all_day"])
