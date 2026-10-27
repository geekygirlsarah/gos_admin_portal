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


def _within_feed_window(days_from_today=7, hour=10):
    """Return an aware datetime inside the feeds' rolling ICS window.

    ``PublicICSFeedView``/``FeedICSView`` only emit events between 30 days ago
    and a year ahead, so events must be positioned relative to *today* rather
    than pinned to a fixed date that eventually ages out of that window.
    """
    day = timezone.localdate() + timedelta(days=days_from_today)
    return timezone.make_aware(
        timezone.datetime.combine(day, timezone.datetime.min.time()).replace(hour=hour),
        timezone.UTC,
    )


class PublicICSViewTests(TestCase):
    def setUp(self):
        self.feed = make_feed(
            name="Public Calendar",
            visibility=CalendarFeed.VISIBILITY_ANONYMOUS,
            slug="public-cal",
        )
        self.start = _within_feed_window()
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
        self.assertIn("X-WR-TIMEZONE:America/New_York", body)
        self.assertIn("END:VCALENDAR", body)

    def test_ics_contains_event(self):
        resp = self.client.get(self.url)
        body = resp.content.decode("utf-8")
        self.assertIn("Public Event", body)
        self.assertIn("BEGIN:VEVENT", body)
        self.assertIn("DTSTART;TZID=America/New_York:", body)
        self.assertIn("DTEND;TZID=America/New_York:", body)

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
            visibility=CalendarFeed.VISIBILITY_MEMBERS,
            slug="members",
            acls={"Mentor": (True, True)},
        )
        self.start = _within_feed_window()
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
        start = _within_feed_window(days_from_today=7, hour=10)
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
        start = _within_feed_window(days_from_today=9, hour=0)
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
        from calendar_feeds.models import EventException

        feed = make_feed(
            visibility=CalendarFeed.VISIBILITY_ANONYMOUS,
            slug="exc-test",
        )
        start = _within_feed_window(days_from_today=7, hour=10)
        event = make_event(
            feed,
            title="Weekly",
            start_datetime=start,
            end_datetime=start + timedelta(hours=1),
            rrule_ical="FREQ=WEEKLY;BYDAY=TU",
        )
        # Delete the *second* weekly occurrence so the EXDATE lands inside the
        # feed's rolling window regardless of when the suite runs.
        deleted_date = (start + timedelta(days=7)).date()
        EventException.objects.create(
            event=event,
            original_date=deleted_date,
            change_type=EventException.CHANGE_DELETED,
        )
        resp = self.client.get(f"/calendar/{feed.slug}/feed.ics")
        body = resp.content.decode("utf-8")
        self.assertIn(f"EXDATE:{deleted_date.strftime('%Y%m%d')}", body)

    def test_localtime_conversion_and_tzid_formatting(self):
        feed = make_feed(
            visibility=CalendarFeed.VISIBILITY_ANONYMOUS,
            slug="tz-test",
        )
        # 2026-10-27 22:00:00 UTC corresponds to 18:00:00 (6:00 PM) EDT
        utc_start = timezone.make_aware(
            timezone.datetime(2026, 10, 27, 22, 0, 0),
            timezone.UTC,
        )
        utc_end = timezone.make_aware(
            timezone.datetime(2026, 10, 27, 23, 30, 0),
            timezone.UTC,
        )
        make_event(
            feed,
            title="Evening Meeting",
            start_datetime=utc_start,
            end_datetime=utc_end,
        )
        resp = self.client.get(f"/calendar/{feed.slug}/feed.ics")
        body = resp.content.decode("utf-8")
        self.assertIn("DTSTART;TZID=America/New_York:20261027T180000", body)
        self.assertIn("DTEND;TZID=America/New_York:20261027T193000", body)

    def test_moved_exception_emitted_with_tzid(self):
        from calendar_feeds.models import EventException

        feed = make_feed(
            visibility=CalendarFeed.VISIBILITY_ANONYMOUS,
            slug="moved-exc-test",
        )
        start = _within_feed_window(days_from_today=7, hour=18)
        event = make_event(
            feed,
            title="Weekly Study",
            start_datetime=start,
            end_datetime=start + timedelta(hours=1),
            rrule_ical="FREQ=WEEKLY;BYDAY=TU",
        )
        target_date = (start + timedelta(days=7)).date()
        new_start = timezone.make_aware(
            timezone.datetime.combine(
                target_date, timezone.datetime.min.time()
            ).replace(hour=19),
            timezone.UTC,
        )
        new_end = new_start + timedelta(hours=1)
        EventException.objects.create(
            event=event,
            original_date=target_date,
            change_type=EventException.CHANGE_MOVED,
            new_start_datetime=new_start,
            new_end_datetime=new_end,
            new_title="Moved Study Session",
        )
        resp = self.client.get(f"/calendar/{feed.slug}/feed.ics")
        body = resp.content.decode("utf-8")
        self.assertIn("RECURRENCE-ID;TZID=America/New_York:", body)
        self.assertIn("SUMMARY:Moved Study Session", body)
        self.assertIn("DTSTART;TZID=America/New_York:", body)
