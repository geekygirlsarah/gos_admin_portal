from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from calendar_feeds.models import CalendarFeed
from calendar_feeds.tests.factories import make_event, make_feed, make_lead_mentor_user


class CalendarActionsTests(TestCase):
    def setUp(self):
        self.feed = make_feed(visibility=CalendarFeed.VISIBILITY_ANONYMOUS)
        self.start = timezone.make_aware(
            timezone.datetime(2026, 9, 1, 10), timezone.UTC
        )
        self.event = make_event(
            self.feed,
            start_datetime=self.start,
            end_datetime=self.start + timedelta(hours=1),
            rrule_ical="FREQ=DAILY;COUNT=2",
        )

    def test_api_exposes_action_permissions_and_occurrence_identity(self):
        url = reverse("calendar_feeds:api_events")
        params = {"start": "2026-09-01", "end": "2026-09-03"}
        anonymous = self.client.get(url, params).json()["events"][0]["extendedProps"]
        self.assertFalse(anonymous["can_write"])
        self.assertEqual(anonymous["original_date"], "2026-09-01")
        self.assertTrue(anonymous["is_recurring"])
        self.client.force_login(make_lead_mentor_user())
        writable = self.client.get(url, params).json()["events"][0]["extendedProps"]
        self.assertTrue(writable["can_write"])

    def test_calendar_includes_separate_series_and_occurrence_actions(self):
        response = self.client.get(reverse("calendar_feeds:calendar_view"))
        self.assertContains(response, "Edit entire series")
        self.assertContains(response, "Delete entire series")
        self.assertContains(response, "Edit this event")
        self.assertContains(response, "Delete this event")
