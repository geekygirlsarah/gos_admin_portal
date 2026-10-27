"""Tests for initial sync on CalendarFeed creation."""

from datetime import date, time

from django.test import TestCase

from calendar_feeds.models import CalendarEvent, CalendarFeed
from calendar_feeds.tests.factories import make_lead_mentor_user, make_program
from outreach.models import OutreachEvent, OutreachShift
from travel.models import TravelEvent


class FeedCreationInitialSyncTests(TestCase):
    def setUp(self):
        self.lead = make_lead_mentor_user()
        self.client.login(username="lead", password="password123")  # nosec B106
        self.program = make_program("Robotics Season")

        # Create existing outreach and travel items prior to feed creation
        self.outreach_event = OutreachEvent.objects.create(
            program=self.program,
            name="Existing Outreach Workshop",
        )
        self.outreach_shift = OutreachShift.objects.create(
            event=self.outreach_event,
            date=date(2026, 11, 1),
            start_time=time(10, 0),
            end_time=time(12, 0),
        )

        self.travel_event = TravelEvent.objects.create(
            program=self.program,
            name="Existing Championship Trip",
            start_date=date(2026, 11, 15),
            end_date=date(2026, 11, 18),
            location_name="Convention Center",
            location_address="100 Main St",
        )

    def test_creating_outreach_feed_runs_initial_sync(self):
        post_data = {
            "name": "Team Outreach",
            "slug": "team-outreach",
            "color": "#0d6efd",
            "visibility": CalendarFeed.VISIBILITY_MEMBERS,
            "source_type": CalendarFeed.SOURCE_OUTREACH,
            "program": self.program.pk,
            "is_active": True,
            "acls-TOTAL_FORMS": "0",
            "acls-INITIAL_FORMS": "0",
            "acls-MIN_NUM_FORMS": "0",
            "acls-MAX_NUM_FORMS": "1000",
        }
        resp = self.client.post("/calendar/feeds/create/", post_data)
        self.assertEqual(resp.status_code, 302)

        feed = CalendarFeed.objects.get(slug="team-outreach")
        self.assertTrue(
            CalendarEvent.objects.filter(
                feed=feed,
                title="Existing Outreach Workshop",
                source_id=str(self.outreach_shift.pk),
            ).exists()
        )

    def test_creating_travel_feed_runs_initial_sync(self):
        post_data = {
            "name": "Team Travel",
            "slug": "team-travel",
            "color": "#fd7e14",
            "visibility": CalendarFeed.VISIBILITY_MEMBERS,
            "source_type": CalendarFeed.SOURCE_TRAVEL,
            "program": self.program.pk,
            "is_active": True,
            "acls-TOTAL_FORMS": "0",
            "acls-INITIAL_FORMS": "0",
            "acls-MIN_NUM_FORMS": "0",
            "acls-MAX_NUM_FORMS": "1000",
        }
        resp = self.client.post("/calendar/feeds/create/", post_data)
        self.assertEqual(resp.status_code, 302)

        feed = CalendarFeed.objects.get(slug="team-travel")
        self.assertTrue(
            CalendarEvent.objects.filter(
                feed=feed,
                title="Existing Championship Trip",
                source_id=str(self.travel_event.pk),
            ).exists()
        )
