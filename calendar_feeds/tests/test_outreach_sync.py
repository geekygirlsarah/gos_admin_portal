"""Tests for outreach → calendar sync signals."""

from datetime import date, time

from django.test import TestCase
from django.utils import timezone

from calendar_feeds.models import CalendarEvent, CalendarFeed
from calendar_feeds.tests.factories import make_feed, make_program
from outreach.models import OutreachEvent, OutreachShift


class OutreachSyncTests(TestCase):
    def setUp(self):
        self.program = make_program("Outreach Program")
        self.feed = make_feed(
            name="Outreach",
            slug="outreach",
            source_type=CalendarFeed.SOURCE_OUTREACH,
            visibility=CalendarFeed.VISIBILITY_ANONYMOUS,
            program=self.program,
        )

    def test_outreach_event_creates_calendar_event(self):
        event = OutreachEvent.objects.create(
            program=self.program,
            name="Community Cleanup",
        )
        OutreachShift.objects.create(
            event=event,
            date=date(2026, 9, 10),
            start_time=time(9, 0),
            end_time=time(12, 0),
        )
        self.assertTrue(
            CalendarEvent.objects.filter(title="Community Cleanup").exists()
        )

    def test_outreach_shift_delete_removes_calendar_event(self):
        event = OutreachEvent.objects.create(
            program=self.program,
            name="Food Drive",
        )
        shift = OutreachShift.objects.create(
            event=event,
            date=date(2026, 9, 12),
            start_time=time(10, 0),
            end_time=time(14, 0),
        )
        cal_event = CalendarEvent.objects.get(source_id=str(shift.pk))
        shift.delete()
        self.assertFalse(CalendarEvent.objects.filter(pk=cal_event.pk).exists())

    def test_outreach_shift_update_updates_calendar_event(self):
        event = OutreachEvent.objects.create(
            program=self.program,
            name="Workshop",
        )
        shift = OutreachShift.objects.create(
            event=event,
            date=date(2026, 9, 15),
            start_time=time(9, 0),
            end_time=time(11, 0),
        )
        cal_event = CalendarEvent.objects.get(source_id=str(shift.pk))
        self.assertEqual(
            timezone.localtime(cal_event.start_datetime).time(), time(9, 0)
        )
        shift.start_time = time(10, 0)
        shift.end_time = time(12, 0)
        shift.save()
        cal_event.refresh_from_db()
        self.assertEqual(
            timezone.localtime(cal_event.start_datetime).time(), time(10, 0)
        )


class NoFeedForProgramTests(TestCase):
    """When no outreach feed exists, signal is a no-op."""

    def test_no_feed_no_error(self):
        program = make_program("No Feed Program")
        OutreachEvent.objects.create(program=program, name="Event")
        self.assertFalse(CalendarEvent.objects.filter(title="Event").exists())
