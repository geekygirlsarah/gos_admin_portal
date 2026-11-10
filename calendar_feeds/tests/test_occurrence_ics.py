from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from django.test import TestCase, override_settings

from calendar_feeds.ics import _build_ics_response
from calendar_feeds.models import EventException
from calendar_feeds.tests.factories import make_event, make_feed
from organizations.models import Organization


@override_settings(TIME_ZONE="America/New_York")
class OccurrenceICSTests(TestCase):
    def setUp(self):
        self.feed = make_feed(slug="occurrences")
        self.start = datetime(2026, 3, 1, 10, tzinfo=ZoneInfo("America/New_York"))
        self.event = make_event(
            self.feed,
            title="Original, title",
            description="Original description",
            location="Original location",
            start_datetime=self.start,
            end_datetime=self.start + timedelta(hours=2),
            rrule_ical="FREQ=WEEKLY",
            exdate_ical="2026-03-08,2026-03-08",
        )

    def exception(self, day, kind, **kwargs):
        return EventException.objects.create(
            event=self.event, original_date=day, change_type=kind, **kwargs
        )

    def components(self):
        lines = _build_ics_response([self.event]).content.decode().splitlines()
        components = []
        current = None
        for line in lines:
            if line == "BEGIN:VEVENT":
                self.assertIsNone(current, "VEVENTs must not be nested")
                current = []
            elif line == "END:VEVENT":
                self.assertIsNotNone(current)
                components.append(current)
                current = None
            elif current is not None:
                current.append(line)
        self.assertIsNone(current)
        return components

    def test_timed_deleted_and_detached_exceptions(self):
        self.exception(date(2026, 3, 8), EventException.CHANGE_DELETED)
        self.exception(
            date(2026, 3, 15),
            EventException.CHANGE_MOVED,
            new_start_datetime=datetime(
                2026, 3, 16, 11, tzinfo=ZoneInfo("America/New_York")
            ),
            new_end_datetime=datetime(
                2026, 3, 16, 13, tzinfo=ZoneInfo("America/New_York")
            ),
        )
        self.exception(
            date(2026, 3, 22),
            EventException.CHANGE_CHANGED,
            new_title="Changed; title",
            new_description="New\ndescription",
        )
        master, moved, changed = self.components()
        self.assertIn("EXDATE:20260308T150000Z", master)
        self.assertIn(f"UID:{self.event.uid}", moved)
        self.assertIn("RECURRENCE-ID:20260315T150000Z", moved)
        self.assertIn("DTSTART;TZID=America/New_York:20260316T110000", moved)
        self.assertIn("DESCRIPTION:Original description", moved)
        self.assertIn("RECURRENCE-ID:20260322T150000Z", changed)
        self.assertIn("DTSTART;TZID=America/New_York:20260322T110000", changed)
        self.assertIn("DTEND;TZID=America/New_York:20260322T130000", changed)
        self.assertIn("SUMMARY:Changed\\; title", changed)
        self.assertIn("DESCRIPTION:New\\ndescription", changed)
        self.assertIn("LOCATION:Original location", changed)

    def test_all_day_exceptions_use_date_values_and_exclusive_end(self):
        self.event.all_day = True
        self.event.exdate_ical = ""
        self.exception(date(2026, 3, 8), EventException.CHANGE_DELETED)
        self.exception(
            date(2026, 3, 15), EventException.CHANGE_CHANGED, new_title="Changed"
        )
        self.exception(
            date(2026, 3, 22),
            EventException.CHANGE_MOVED,
            new_start_datetime=datetime(
                2026, 3, 23, 10, tzinfo=ZoneInfo("America/New_York")
            ),
            new_end_datetime=datetime(
                2026, 3, 24, 10, tzinfo=ZoneInfo("America/New_York")
            ),
        )
        master, changed, moved = self.components()
        self.assertIn("EXDATE;VALUE=DATE:20260308", master)
        self.assertIn("RECURRENCE-ID;VALUE=DATE:20260315", changed)
        self.assertIn("DTSTART;VALUE=DATE:20260315", changed)
        self.assertIn("DTEND;VALUE=DATE:20260316", changed)
        self.assertIn("DTSTART;VALUE=DATE:20260323", moved)
        self.assertIn("DTEND;VALUE=DATE:20260325", moved)

    def test_foreign_exception_is_not_serialized(self):
        other = Organization.objects.create(name="Other", slug="other-occurrence")
        self.exception(
            date(2026, 3, 15),
            EventException.CHANGE_CHANGED,
            organization=other,
            new_title="Foreign secret",
        )
        components = self.components()
        self.assertEqual(len(components), 1)
        self.assertNotIn("Foreign secret", "\n".join(components[0]))

    def test_stale_exceptions_after_rrule_edit_are_retained_but_not_exported(self):
        self.event.rrule_ical = "FREQ=WEEKLY;COUNT=3"
        self.event.exdate_ical = ""
        self.exception(
            date(2026, 3, 8), EventException.CHANGE_CHANGED, new_title="Valid"
        )
        self.exception(date(2026, 3, 15), EventException.CHANGE_DELETED)
        self.exception(date(2026, 3, 22), EventException.CHANGE_DELETED)
        self.exception(
            date(2026, 3, 29), EventException.CHANGE_CHANGED, new_title="Stale"
        )
        self.exception(
            date(2026, 4, 5),
            EventException.CHANGE_MOVED,
            new_start_datetime=datetime(
                2026, 4, 6, 10, tzinfo=ZoneInfo("America/New_York")
            ),
            new_end_datetime=datetime(
                2026, 4, 6, 12, tzinfo=ZoneInfo("America/New_York")
            ),
        )
        master, changed = self.components()
        self.assertIn("EXDATE:20260315T150000Z", master)
        self.assertNotIn("20260322", "\n".join(master))
        self.assertIn("SUMMARY:Valid", changed)
        self.assertEqual(self.event.exceptions.count(), 5)

    def test_stale_all_day_exceptions_after_weekday_change(self):
        self.event.all_day = True
        self.event.rrule_ical = "FREQ=WEEKLY;BYDAY=MO"
        self.event.exdate_ical = ""
        self.exception(date(2026, 3, 8), EventException.CHANGE_DELETED)
        self.exception(
            date(2026, 3, 15), EventException.CHANGE_CHANGED, new_title="Stale"
        )
        self.exception(date(2026, 3, 9), EventException.CHANGE_DELETED)
        self.exception(
            date(2026, 3, 16), EventException.CHANGE_CHANGED, new_title="Valid"
        )
        master, changed = self.components()
        self.assertIn("EXDATE;VALUE=DATE:20260309", master)
        self.assertNotIn("20260308", "\n".join(master))
        self.assertIn("RECURRENCE-ID;VALUE=DATE:20260316", changed)
        self.assertIn("SUMMARY:Valid", changed)
        self.assertEqual(self.event.exceptions.count(), 4)

    def test_explicit_exclusion_wins_over_changed_exception(self):
        self.exception(
            date(2026, 3, 8), EventException.CHANGE_CHANGED, new_title="Hidden"
        )
        components = self.components()
        self.assertEqual(len(components), 1)
        self.assertIn("EXDATE:20260308T150000Z", components[0])

    def test_utc_date_keys_across_midnight_and_dst(self):
        self.event.start_datetime = datetime(
            2026, 3, 1, 23, 30, tzinfo=ZoneInfo("America/New_York")
        )
        self.event.end_datetime = datetime(
            2026, 3, 2, 0, 30, tzinfo=ZoneInfo("America/New_York")
        )
        self.event.exdate_ical = "2026-03-16"
        self.exception(date(2026, 3, 9), EventException.CHANGE_DELETED)
        self.exception(
            date(2026, 3, 23), EventException.CHANGE_CHANGED, new_title="Changed"
        )
        self.exception(
            date(2026, 3, 30),
            EventException.CHANGE_MOVED,
            new_start_datetime=datetime(
                2026, 3, 30, 12, tzinfo=ZoneInfo("America/New_York")
            ),
            new_end_datetime=datetime(
                2026, 3, 30, 13, tzinfo=ZoneInfo("America/New_York")
            ),
        )
        master, changed, moved = self.components()
        self.assertIn("DTSTART:20260302T043000Z", master)
        self.assertIn("EXDATE:20260309T043000Z,20260316T043000Z", master)
        self.assertIn("RECURRENCE-ID:20260323T043000Z", changed)
        self.assertIn("DTSTART;TZID=America/New_York:20260323T003000", changed)
        self.assertIn("DTEND;TZID=America/New_York:20260323T013000", changed)
        self.assertIn("RECURRENCE-ID:20260330T043000Z", moved)

    def test_utc_date_key_can_shift_to_previous_local_date_after_dst(self):
        self.event.start_datetime = datetime(
            2026, 3, 1, 22, 30, tzinfo=ZoneInfo("America/New_York")
        )
        self.event.end_datetime = datetime(
            2026, 3, 1, 23, 30, tzinfo=ZoneInfo("America/New_York")
        )
        self.event.exdate_ical = ""
        self.exception(
            date(2026, 3, 9), EventException.CHANGE_CHANGED, new_title="Changed"
        )
        master, changed = self.components()
        self.assertIn("DTSTART:20260302T033000Z", master)
        self.assertIn("RECURRENCE-ID:20260309T033000Z", changed)
        self.assertIn("DTSTART;TZID=America/New_York:20260308T233000", changed)
        self.assertIn("DTEND;TZID=America/New_York:20260309T003000", changed)
