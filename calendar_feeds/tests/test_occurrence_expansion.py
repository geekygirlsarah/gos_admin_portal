from datetime import date, timedelta

from django.test import TestCase
from django.utils import timezone

from calendar_feeds.models import EventException
from calendar_feeds.tests.factories import make_event, make_feed
from calendar_feeds.utils import expand_events_for_range
from organizations.models import Organization


class OccurrenceExpansionTests(TestCase):
    def setUp(self):
        self.feed = make_feed()
        self.start = timezone.make_aware(
            timezone.datetime(2026, 9, 1, 10), timezone.UTC
        )
        self.event = make_event(
            self.feed,
            start_datetime=self.start,
            rrule_ical="FREQ=DAILY;COUNT=5",
            title="Original",
            location="Original location",
            description="Original description",
        )

    def move(self, original, destination, **kwargs):
        start = self.start + timedelta(days=destination - 1)
        return EventException.objects.create(
            event=self.event,
            original_date=date(2026, 9, original),
            change_type=EventException.CHANGE_MOVED,
            new_start_datetime=start,
            new_end_datetime=start + timedelta(hours=2),
            **kwargs,
        )

    def expand(self, first, last):
        return expand_events_for_range(
            self.feed, date(2026, 9, first), date(2026, 9, last)
        )

    def test_move_into_range_applies_identity_and_edits(self):
        exc = self.move(
            1,
            8,
            new_title="Edited",
            new_location="New place",
            new_description="New details",
        )
        (instance,) = self.expand(8, 8)
        self.assertEqual(instance["date"], date(2026, 9, 8))
        self.assertEqual(instance["original_date"], date(2026, 9, 1))
        self.assertEqual(instance["exception_pk"], exc.pk)
        self.assertEqual(
            (instance["title"], instance["location"], instance["description"]),
            ("Edited", "New place", "New details"),
        )
        self.assertEqual(
            instance["end_datetime"] - instance["start_datetime"], timedelta(hours=2)
        )

    def test_move_out_and_blank_overrides(self):
        exc = self.move(2, 8, new_title="Edited")
        self.assertEqual(self.expand(2, 2), [])
        (instance,) = self.expand(8, 8)
        self.assertEqual(instance["exception_pk"], exc.pk)
        self.assertEqual(
            (instance["title"], instance["location"], instance["description"]),
            ("Edited", "Original location", "Original description"),
        )

    def test_changed_and_nonrecurring_identity(self):
        exc = EventException.objects.create(
            event=self.event,
            original_date=date(2026, 9, 2),
            change_type=EventException.CHANGE_CHANGED,
            new_title="Changed",
        )
        single = make_event(self.feed, start_datetime=self.start)
        instances = self.expand(1, 2)
        changed = next(i for i in instances if i["exception_pk"] == exc.pk)
        self.assertEqual(
            (changed["original_date"], changed["title"]), (date(2026, 9, 2), "Changed")
        )
        for instance in instances:
            if instance["pk"] == single.pk or instance["date"] == date(2026, 9, 1):
                self.assertEqual(instance["original_date"], instance["date"])
                self.assertIsNone(instance["exception_pk"])

    def test_excluded_stale_and_foreign_moves_are_ignored(self):
        self.event.exdate_ical = "2026-09-01"
        self.event.save()
        self.move(1, 8)
        self.move(7, 8)
        foreign = Organization.objects.create(name="Other", slug="other-occurrence")
        self.move(2, 8, organization=foreign)
        self.assertEqual(self.expand(8, 8), [])
        (instance,) = self.expand(2, 2)
        self.assertIsNone(instance["exception_pk"])

    def test_exception_queries_do_not_scale_with_occurrences(self):
        with self.assertNumQueries(2):
            instances = self.expand(1, 5)
        self.assertEqual(len(instances), 5)

    def test_timed_occurrences_preserve_duration_across_utc_midnight(self):
        self.event.start_datetime = self.start.replace(hour=23)
        self.event.end_datetime = self.event.start_datetime + timedelta(hours=2)
        self.event.save()
        exc = EventException.objects.create(
            event=self.event,
            original_date=date(2026, 9, 2),
            change_type=EventException.CHANGE_CHANGED,
            new_title="Changed",
        )

        instances = self.expand(1, 2)
        self.assertEqual(len(instances), 2)
        for instance in instances:
            expected_start = self.event.start_datetime + timedelta(
                days=(instance["date"] - date(2026, 9, 1)).days
            )
            self.assertEqual(instance["start_datetime"], expected_start)
            self.assertEqual(
                instance["end_datetime"], expected_start + timedelta(hours=2)
            )
        self.assertEqual(instances[1]["exception_pk"], exc.pk)

    def test_timed_occurrence_preserves_multi_day_duration(self):
        self.event.end_datetime = self.start + timedelta(days=2, hours=3)
        self.event.save()
        (instance,) = self.expand(2, 2)
        self.assertEqual(instance["start_datetime"], self.start + timedelta(days=1))
        self.assertEqual(
            instance["end_datetime"], self.start + timedelta(days=3, hours=3)
        )
