"""Tests for travel → calendar sync signals."""

from datetime import date

from django.test import TestCase

from calendar_feeds.models import CalendarEvent, CalendarFeed
from calendar_feeds.tests.factories import make_feed, make_program
from travel.models import TravelDeparture, TravelEvent


class TravelSyncTests(TestCase):
    def setUp(self):
        self.program = make_program("Travel Program")
        self.feed = make_feed(
            name="Travel Feed",
            slug="travel",
            source_type=CalendarFeed.SOURCE_TRAVEL,
            visibility=CalendarFeed.VISIBILITY_MEMBERS,
            program=self.program,
        )

    def test_travel_event_creates_calendar_event(self):
        travel = TravelEvent.objects.create(
            program=self.program,
            name="Regional Competition",
            start_date=date(2026, 10, 20),
            end_date=date(2026, 10, 22),
            location_name="Sports Arena",
            location_address="123 Arena Way",
            description="Bring safety glasses and team shirts.",
        )
        self.assertTrue(
            CalendarEvent.objects.filter(
                feed=self.feed,
                title="Regional Competition",
                source_id=str(travel.pk),
            ).exists()
        )
        cal_event = CalendarEvent.objects.get(source_id=str(travel.pk))
        self.assertTrue(cal_event.all_day)
        self.assertEqual(cal_event.location, "Sports Arena, 123 Arena Way")
        self.assertIn("Bring safety glasses", cal_event.description)

    def test_travel_event_update_updates_calendar_event(self):
        travel = TravelEvent.objects.create(
            program=self.program,
            name="Championships",
            start_date=date(2026, 11, 10),
            end_date=date(2026, 11, 12),
        )
        cal_event = CalendarEvent.objects.get(source_id=str(travel.pk))
        self.assertEqual(cal_event.title, "Championships")

        travel.name = "World Championships"
        travel.save()

        cal_event.refresh_from_db()
        self.assertEqual(cal_event.title, "World Championships")

    def test_travel_event_delete_removes_calendar_event(self):
        travel = TravelEvent.objects.create(
            program=self.program,
            name="Pre-season Scrimmage",
            start_date=date(2026, 9, 20),
            end_date=date(2026, 9, 21),
        )
        self.assertTrue(CalendarEvent.objects.filter(source_id=str(travel.pk)).exists())
        travel.delete()
        self.assertFalse(
            CalendarEvent.objects.filter(source_id=str(travel.pk)).exists()
        )

    def test_travel_departure_sync_updates_description(self):
        travel = TravelEvent.objects.create(
            program=self.program,
            name="Bus Trip",
            start_date=date(2026, 12, 1),
            end_date=date(2026, 12, 2),
            description="Trip info",
        )
        TravelDeparture.objects.create(
            event=travel,
            label="Bus 1",
            leave_date=date(2026, 12, 1),
            meeting_location="High School Parking Lot",
        )
        cal_event = CalendarEvent.objects.get(source_id=str(travel.pk))
        self.assertIn("Bus 1", cal_event.description)
        self.assertIn("High School Parking Lot", cal_event.description)
