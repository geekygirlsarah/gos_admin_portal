from datetime import date, time, timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from calendar_feeds.models import CalendarEvent, CalendarFeed
from calendar_feeds.sources.outreach import initial_sync as initial_outreach_sync
from calendar_feeds.sources.outreach import sync_outreach_shift
from calendar_feeds.sources.travel import initial_sync as initial_travel_sync
from calendar_feeds.sources.travel import sync_travel_event
from organizations.models import Organization
from outreach.models import OutreachEvent, OutreachShift
from programs.models import Program
from travel.models import TravelDeparture, TravelEvent


class CalendarSourceTenantIsolationTests(TestCase):
    def setUp(self):
        self.alpha = Organization.objects.get(slug="gos")
        self.beta = Organization.objects.create(
            name="Calendar Sync Other Organization",
            slug="calendar-sync-other",
            subdomain="calendar-sync-other",
        )
        self.alpha_program = Program.objects.create(
            organization=self.alpha, name="Alpha Program"
        )
        self.beta_program = Program.objects.create(
            organization=self.beta, name="Beta Program"
        )
        self.alpha_travel_feed = self.make_feed(
            self.alpha,
            CalendarFeed.SOURCE_TRAVEL,
            "Alpha Travel",
            program=self.alpha_program,
        )
        self.alpha_outreach_feed = self.make_feed(
            self.alpha,
            CalendarFeed.SOURCE_OUTREACH,
            "Alpha Outreach",
            program=self.alpha_program,
        )

    def make_feed(self, organization, source_type, name, *, program=None):
        return CalendarFeed.objects.create(
            organization=organization,
            name=name,
            slug=name.lower().replace(" ", "-"),
            source_type=source_type,
            program=program,
        )

    def make_travel_event(self, organization, program, name):
        return TravelEvent.objects.create(
            organization=organization,
            program=program,
            name=name,
            start_date=date(2026, 11, 15),
            end_date=date(2026, 11, 16),
        )

    def make_outreach_shift(self, organization, program, name):
        event = OutreachEvent.objects.create(
            organization=organization,
            program=program,
            name=name,
            location_name="Community Center",
            location_address="1 Main Street",
        )
        return OutreachShift.objects.create(
            organization=organization,
            event=event,
            date=date(2026, 11, 15),
            start_time=time(10),
            end_time=time(12),
        )

    def make_calendar_event(
        self, feed, source, *, title="Existing event", description=""
    ):
        existing = CalendarEvent.objects.filter(
            feed=feed, source_id=str(source.pk)
        ).first()
        if existing:
            return existing

        start = timezone.now() + timedelta(days=1)
        return CalendarEvent.objects.create(
            organization=feed.organization,
            feed=feed,
            title=title,
            description=description,
            start_datetime=start,
            end_datetime=start + timedelta(hours=1),
            source_id=str(source.pk),
        )

    def test_programless_travel_initial_sync_only_uses_feed_organization(self):
        feed = self.make_feed(
            self.alpha,
            CalendarFeed.SOURCE_TRAVEL,
            "Alpha Organization Travel",
        )
        alpha_trip = self.make_travel_event(self.alpha, None, "Alpha trip")
        beta_trip = self.make_travel_event(self.beta, None, "Beta trip")

        with patch("calendar_feeds.sources.travel.sync_travel_event") as sync:
            count = initial_travel_sync(feed)

        self.assertEqual(count, 1)
        sync.assert_called_once_with(alpha_trip, feed)
        self.assertNotEqual(alpha_trip.pk, beta_trip.pk)

    def test_travel_initial_sync_rejects_program_owned_by_another_organization(self):
        feed = self.make_feed(
            self.beta,
            CalendarFeed.SOURCE_TRAVEL,
            "Foreign Travel Program Feed",
            program=self.alpha_program,
        )
        trip = self.make_travel_event(self.alpha, self.alpha_program, "Alpha trip")

        with patch("calendar_feeds.sources.travel.sync_travel_event") as sync:
            count = initial_travel_sync(feed)

        self.assertEqual(count, 0)
        sync.assert_not_called()
        self.assertEqual(trip.organization, self.alpha)

    def test_travel_sync_does_not_update_foreign_feed(self):
        trip = self.make_travel_event(self.alpha, self.alpha_program, "Alpha trip")
        foreign_feed = self.make_feed(
            self.beta,
            CalendarFeed.SOURCE_TRAVEL,
            "Foreign Travel Feed",
            program=self.alpha_program,
        )
        calendar_event = self.make_calendar_event(
            foreign_feed, trip, title="Unchanged foreign event"
        )

        result = sync_travel_event(trip, foreign_feed)

        self.assertIsNone(result)
        calendar_event.refresh_from_db()
        self.assertEqual(calendar_event.title, "Unchanged foreign event")

    def test_travel_sync_preserves_foreign_owned_calendar_row(self):
        trip = self.make_travel_event(self.alpha, self.alpha_program, "Alpha trip")
        calendar_event = self.make_calendar_event(self.alpha_travel_feed, trip)
        CalendarEvent.objects.filter(pk=calendar_event.pk).update(
            organization=self.beta, title="Unchanged foreign row"
        )

        self.assertIsNone(sync_travel_event(trip, self.alpha_travel_feed))
        calendar_event.refresh_from_db()
        self.assertEqual(calendar_event.organization_id, self.beta.pk)
        self.assertEqual(calendar_event.title, "Unchanged foreign row")
        self.assertEqual(
            CalendarEvent.objects.filter(
                feed=self.alpha_travel_feed, source_id=str(trip.pk)
            ).count(),
            1,
        )

    def test_travel_sync_omits_foreign_departures(self):
        trip = self.make_travel_event(self.alpha, self.alpha_program, "Alpha trip")
        TravelDeparture.objects.create(
            organization=self.alpha,
            event=trip,
            label="Alpha departure",
            leave_date=trip.start_date,
        )
        TravelDeparture.objects.create(
            organization=self.beta,
            event=trip,
            label="Foreign departure secret",
            leave_date=trip.start_date,
        )
        self.make_calendar_event(self.alpha_travel_feed, trip)

        calendar_event = sync_travel_event(trip, self.alpha_travel_feed)

        self.assertIn("Alpha departure", calendar_event.description)
        self.assertNotIn("Foreign departure secret", calendar_event.description)

    def test_travel_delete_does_not_remove_foreign_feed_event(self):
        trip = self.make_travel_event(self.alpha, self.alpha_program, "Alpha trip")
        foreign_feed = self.make_feed(
            self.beta,
            CalendarFeed.SOURCE_TRAVEL,
            "Foreign Travel Feed",
            program=self.alpha_program,
        )
        calendar_event = self.make_calendar_event(foreign_feed, trip)

        trip.delete()

        self.assertTrue(CalendarEvent.objects.filter(pk=calendar_event.pk).exists())

    def test_outreach_initial_sync_ignores_foreign_owned_shift(self):
        alpha_shift = self.make_outreach_shift(
            self.alpha, self.alpha_program, "Alpha outreach"
        )
        beta_shift = self.make_outreach_shift(
            self.beta, self.alpha_program, "Foreign outreach"
        )

        with patch("calendar_feeds.sources.outreach.sync_outreach_shift") as sync:
            count = initial_outreach_sync(self.alpha_outreach_feed)

        self.assertEqual(count, 1)
        sync.assert_called_once_with(alpha_shift, self.alpha_outreach_feed)
        self.assertNotEqual(alpha_shift.pk, beta_shift.pk)

    def test_outreach_sync_does_not_update_foreign_feed(self):
        shift = self.make_outreach_shift(
            self.alpha, self.alpha_program, "Alpha outreach"
        )
        foreign_feed = self.make_feed(
            self.beta,
            CalendarFeed.SOURCE_OUTREACH,
            "Foreign Outreach Feed",
            program=self.alpha_program,
        )
        calendar_event = self.make_calendar_event(
            foreign_feed, shift, title="Unchanged foreign event"
        )

        result = sync_outreach_shift(shift, foreign_feed)

        self.assertIsNone(result)
        calendar_event.refresh_from_db()
        self.assertEqual(calendar_event.title, "Unchanged foreign event")

    def test_outreach_sync_preserves_foreign_owned_calendar_row(self):
        shift = self.make_outreach_shift(
            self.alpha, self.alpha_program, "Alpha outreach"
        )
        calendar_event = self.make_calendar_event(self.alpha_outreach_feed, shift)
        CalendarEvent.objects.filter(pk=calendar_event.pk).update(
            organization=self.beta, title="Unchanged foreign row"
        )

        self.assertIsNone(sync_outreach_shift(shift, self.alpha_outreach_feed))
        calendar_event.refresh_from_db()
        self.assertEqual(calendar_event.organization_id, self.beta.pk)
        self.assertEqual(calendar_event.title, "Unchanged foreign row")
        self.assertEqual(
            CalendarEvent.objects.filter(
                feed=self.alpha_outreach_feed, source_id=str(shift.pk)
            ).count(),
            1,
        )

    def test_outreach_delete_does_not_remove_foreign_feed_event(self):
        shift = self.make_outreach_shift(
            self.alpha, self.alpha_program, "Alpha outreach"
        )
        foreign_feed = self.make_feed(
            self.beta,
            CalendarFeed.SOURCE_OUTREACH,
            "Foreign Outreach Feed",
            program=self.alpha_program,
        )
        calendar_event = self.make_calendar_event(foreign_feed, shift)

        shift.delete()

        self.assertTrue(CalendarEvent.objects.filter(pk=calendar_event.pk).exists())
