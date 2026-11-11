from datetime import date, datetime, timedelta

from django.http import Http404
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from calendar_feeds.models import CalendarFeed, EventException
from calendar_feeds.tests.factories import make_event, make_feed, make_lead_mentor_user
from calendar_feeds.views import _occurrence
from organizations.models import Organization


class OccurrenceEditorTests(TestCase):
    def setUp(self):
        self.user = make_lead_mentor_user()
        self.feed = make_feed(visibility=CalendarFeed.VISIBILITY_ANONYMOUS)
        start = timezone.make_aware(datetime(2026, 9, 1, 10), timezone.UTC)
        self.event = make_event(
            self.feed,
            start_datetime=start,
            end_datetime=start + timedelta(hours=1),
            rrule_ical="FREQ=WEEKLY;COUNT=3",
            location="Room",
        )
        self.client.force_login(self.user)

    def url(self, name, day="2026-09-08"):
        return reverse(
            "calendar_feeds:" + name, kwargs={"pk": self.event.pk, "original_date": day}
        )

    def test_edit_and_delete_occurrence(self):
        response = self.client.post(
            self.url("occurrence_edit"),
            {
                "title": "Changed",
                "location": "Room",
                "description": "",
                "start_datetime": "2026-09-09T10:00",
                "end_datetime": "2026-09-09T11:00",
            },
        )
        self.assertEqual(response.status_code, 302)
        exception = EventException.objects.get(
            event=self.event, original_date=date(2026, 9, 8)
        )
        self.assertEqual(exception.change_type, EventException.CHANGE_MOVED)
        self.assertEqual(exception.new_title, "Changed")
        self.assertEqual(
            self.client.get(self.url("occurrence_delete")).status_code, 200
        )
        self.assertEqual(
            self.client.post(self.url("occurrence_delete")).status_code, 302
        )
        exception.refresh_from_db()
        self.assertEqual(exception.change_type, EventException.CHANGE_DELETED)

    def test_invalid_and_excluded_dates(self):
        request = RequestFactory().get("/")
        request.user = self.user
        request.organization = self.feed.organization
        with self.assertRaises(Http404):
            _occurrence(request, self.event.pk, "2026-09-09")
        self.event.exdate_ical = "2026-09-08"
        self.event.save()
        with self.assertRaises(Http404):
            _occurrence(request, self.event.pk, "2026-09-08")

    def test_foreign_exception_is_not_overwritten(self):
        other = Organization.objects.create(name="Other", slug="other-occurrence")
        exception = EventException.objects.create(
            organization=other,
            event=self.event,
            original_date=date(2026, 9, 8),
            change_type=EventException.CHANGE_CHANGED,
        )
        self.client.post(self.url("occurrence_delete"))
        exception.refresh_from_db()
        self.assertEqual(exception.change_type, EventException.CHANGE_CHANGED)

    def test_api_original_identity_and_permissions(self):
        response = self.client.get(
            reverse("calendar_feeds:api_events"),
            {"start": "2026-09-01", "end": "2026-09-30"},
        )
        item = response.json()["events"][1]
        self.assertEqual(item["id"], f"{self.event.pk}-2026-09-08")
        self.assertEqual(item["extendedProps"]["original_date"], "2026-09-08")
        self.assertTrue(item["extendedProps"]["is_recurring"])
        self.assertTrue(item["extendedProps"]["can_write"])
        self.assertIsNone(item["extendedProps"]["exception_pk"])

    def test_read_only_and_anonymous_cannot_edit(self):
        self.feed.source_type = CalendarFeed.SOURCE_OUTREACH
        self.feed.save()
        self.client.post(self.url("occurrence_delete"))
        self.assertFalse(EventException.objects.filter(event=self.event).exists())
        item = self.client.get(
            reverse("calendar_feeds:api_events"),
            {"start": "2026-09-01", "end": "2026-09-30"},
        ).json()["events"][0]
        self.assertFalse(item["extendedProps"]["can_write"])
        self.client.logout()
        self.client.post(self.url("occurrence_delete"))
        self.assertFalse(EventException.objects.filter(event=self.event).exists())

    def test_cannot_clear_inherited_location(self):
        response = self.client.post(
            self.url("occurrence_edit"),
            {
                "title": "Changed",
                "location": "",
                "description": "",
                "start_datetime": "2026-09-08T10:00",
                "end_datetime": "2026-09-08T11:00",
            },
        )
        self.assertContains(response, "Inherited text cannot be cleared")
        self.assertFalse(EventException.objects.filter(event=self.event).exists())

    def test_unchanged_fields_continue_to_follow_series(self):
        start = timezone.localtime(self.event.start_datetime + timedelta(days=7))
        response = self.client.post(
            self.url("occurrence_edit"),
            {
                "title": "Changed",
                "location": "Room",
                "description": "",
                "start_datetime": start.strftime("%Y-%m-%dT%H:%M"),
                "end_datetime": (start + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"),
            },
        )
        self.assertEqual(response.status_code, 302)
        exception = EventException.objects.get(event=self.event)
        self.assertEqual(exception.change_type, EventException.CHANGE_CHANGED)
        self.assertEqual(exception.new_title, "Changed")
        self.assertEqual(exception.new_location, "")
        self.event.location = "New room"
        self.event.save()
        instances = self.feed.get_events_for_date_range(
            date(2026, 9, 8), date(2026, 9, 8)
        )
        self.assertEqual(instances[0]["location"], "New room")
        self.assertEqual(instances[0]["title"], "Changed")

    def test_api_moved_occurrence_keeps_original_identity(self):
        start = timezone.make_aware(datetime(2026, 9, 9, 10), timezone.UTC)
        exception = EventException.objects.create(
            event=self.event,
            organization=self.feed.organization,
            original_date=date(2026, 9, 8),
            change_type=EventException.CHANGE_MOVED,
            new_start_datetime=start,
            new_end_datetime=start + timedelta(hours=1),
            new_title="Moved title",
        )
        items = self.client.get(
            reverse("calendar_feeds:api_events"),
            {"start": "2026-09-09", "end": "2026-09-09"},
        ).json()["events"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], f"{self.event.pk}-2026-09-08")
        self.assertEqual(items[0]["title"], "Moved title")
        self.assertEqual(items[0]["extendedProps"]["original_date"], "2026-09-08")
        self.assertEqual(items[0]["extendedProps"]["exception_pk"], exception.pk)
