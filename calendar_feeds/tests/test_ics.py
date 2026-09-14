"""Tests for ICS feed endpoints."""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from calendar_feeds.models import CalendarFeed
from calendar_feeds.tests.factories import (
    make_event,
    make_feed,
    make_lead_mentor_user,
    make_mentor_user,
    make_parent_user,
)


class PublicICSViewTests(TestCase):
    def setUp(self):
        self.feed = make_feed(
            name="Public Calendar",
            visibility=CalendarFeed.VISIBILITY_ANONYMOUS,
            slug="public-cal",
        )
        self.start = timezone.make_aware(
            timezone.datetime(2026, 9, 3, 10, 0), timezone.UTC
        )
        make_event(
            self.feed,
            title="Public Event",
            start_datetime=self.start,
            end_datetime=self.start + timedelta(hours=1),
        )
        self.url = f"/calendar/{self.feed.slug}/feed.ics"

    def test_returns_200_with_ics_content_type(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/calendar", resp["Content-Type"])

    def test_ics_has_calendar_header(self):
        resp = self.client.get(self.url)
        body = resp.content.decode("utf-8")
        self.assertIn("BEGIN:VCALENDAR", body)
        self.assertIn("VERSION:2.0", body)
        self.assertIn("PRODID", body)
        self.assertIn("END:VCALENDAR", body)

    def test_ics_contains_event(self):
        resp = self.client.get(self.url)
        body = resp.content.decode("utf-8")
        self.assertIn("Public Event", body)
        self.assertIn("BEGIN:VEVENT", body)

    def test_slug_not_found_returns_404(self):
        resp = self.client.get("/calendar/nonexistent/feed.ics")
        self.assertEqual(resp.status_code, 404)

    def test_public_feed_available_via_aggregate_url(self):
        resp = self.client.get("/calendar/feed.ics")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Public Event", resp.content.decode("utf-8"))


class FeedICSViewTests(TestCase):
    def setUp(self):
        self.feed = make_feed(
            name="Members Feed",
            visibility=CalendarFeed.VISIBILITY_INVITE_ONLY,
            slug="members",
            acls={"Mentor": (True, True)},
        )
        self.start = timezone.make_aware(
            timezone.datetime(2026, 9, 3, 10, 0), timezone.UTC
        )
        make_event(
            self.feed,
            title="Private Event",
            start_datetime=self.start,
            end_datetime=self.start + timedelta(hours=1),
        )
        self.url = f"/calendar/{self.feed.slug}/feed.ics"
        self.lead = make_lead_mentor_user()
        self.mentor = make_mentor_user()
        self.parent = make_parent_user()

    def test_anonymous_rejected(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 401)

    def test_authorized_user_can_download(self):
        self.client.login(username="mentor", password="password123")  # nosec B106
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode("utf-8")
        self.assertIn("Private Event", body)

    def test_no_acl_role_cannot_download(self):
        self.client.login(username="parent", password="password123")  # nosec B106
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 403)

    def test_lead_can_download_any(self):
        self.client.login(username="lead", password="password123")  # nosec B106
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)


class ICSEventFormatTests(TestCase):
    def test_recurring_event_has_rrule(self):
        feed = make_feed(
            visibility=CalendarFeed.VISIBILITY_ANONYMOUS,
            slug="recur-test",
        )
        start = timezone.make_aware(timezone.datetime(2026, 9, 1, 10, 0), timezone.UTC)
        make_event(
            feed,
            title="Weekly Meeting",
            start_datetime=start,
            end_datetime=start + timedelta(hours=1),
            rrule_ical="FREQ=WEEKLY;BYDAY=TU",
        )
        resp = self.client.get(f"/calendar/{feed.slug}/feed.ics")
        body = resp.content.decode("utf-8")
        self.assertIn("RRULE:FREQ=WEEKLY;BYDAY=TU", body)

    def test_all_day_event_has_date_format(self):
        feed = make_feed(
            visibility=CalendarFeed.VISIBILITY_ANONYMOUS,
            slug="allday-test",
        )
        start = timezone.make_aware(timezone.datetime(2026, 9, 5, 0, 0), timezone.UTC)
        make_event(
            feed,
            title="All Day",
            start_datetime=start,
            end_datetime=start + timedelta(days=1),
            all_day=True,
        )
        resp = self.client.get(f"/calendar/{feed.slug}/feed.ics")
        body = resp.content.decode("utf-8")
        self.assertIn("DTSTART;VALUE=DATE", body)

    def test_deleted_exception_emitted_as_exdate(self):
        from datetime import date

        from calendar_feeds.models import EventException

        feed = make_feed(
            visibility=CalendarFeed.VISIBILITY_ANONYMOUS,
            slug="exc-test",
        )
        start = timezone.make_aware(timezone.datetime(2026, 9, 1, 10, 0), timezone.UTC)
        event = make_event(
            feed,
            title="Weekly",
            start_datetime=start,
            end_datetime=start + timedelta(hours=1),
            rrule_ical="FREQ=WEEKLY;BYDAY=TU",
        )
        EventException.objects.create(
            event=event,
            original_date=date(2026, 9, 8),
            change_type=EventException.CHANGE_DELETED,
        )
        resp = self.client.get(f"/calendar/{feed.slug}/feed.ics")
        body = resp.content.decode("utf-8")
        self.assertIn("EXDATE:20260908", body)
