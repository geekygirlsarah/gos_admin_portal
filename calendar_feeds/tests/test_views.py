"""Tests for calendar_feeds views."""

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from calendar_feeds.models import CalendarEvent, CalendarFeed, EventException
from calendar_feeds.tests.factories import (
    make_event,
    make_feed,
    make_lead_mentor_user,
    make_mentor_user,
    make_parent_user,
    make_student_user,
)


class CalendarViewTests(TestCase):
    def setUp(self):
        self.url = reverse("calendar_feeds:calendar_view")
        self.lead = make_lead_mentor_user()
        self.mentor = make_mentor_user()
        self.parent = make_parent_user()
        self.student_user, self.student = make_student_user()

    def test_anonymous_sees_only_public_feeds(self):
        make_feed(name="Public", visibility=CalendarFeed.VISIBILITY_ANONYMOUS)
        make_feed(name="Private", visibility=CalendarFeed.VISIBILITY_MEMBERS)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        feed_names = [f.name for f in resp.context["feeds"]]
        self.assertIn("Public", feed_names)
        self.assertNotIn("Private", feed_names)

    def test_lead_mentor_sees_all(self):
        make_feed(name="Invite Only", visibility=CalendarFeed.VISIBILITY_INVITE_ONLY)
        make_feed(name="Public", visibility=CalendarFeed.VISIBILITY_ANONYMOUS)
        self.client.login(username="lead", password="password123")  # nosec B106
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.context["feeds"]), 2)

    def test_mentor_with_acl_can_read(self):
        make_feed(
            name="Members",
            visibility=CalendarFeed.VISIBILITY_INVITE_ONLY,
            acls={"Mentor": (True, True)},
        )
        self.client.login(username="mentor", password="password123")  # nosec B106
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.context["feeds"]), 1)

    def test_parent_without_acl_cannot_see_invite_only(self):
        make_feed(
            name="Mentors Only",
            visibility=CalendarFeed.VISIBILITY_INVITE_ONLY,
            acls={"Mentor": (True, True)},
        )
        self.client.login(username="parent", password="password123")  # nosec B106
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.context["feeds"]), 0)


class CalendarEventsAPITests(TestCase):
    def setUp(self):
        self.url = reverse("calendar_feeds:api_events")
        self.feed = make_feed(visibility=CalendarFeed.VISIBILITY_ANONYMOUS)
        self.start = timezone.make_aware(
            timezone.datetime(2026, 9, 3, 10, 0), timezone.UTC
        )
        make_event(
            self.feed,
            title="Recurring",
            start_datetime=self.start,
            end_datetime=self.start + timedelta(hours=1),
            rrule_ical="FREQ=WEEKLY;BYDAY=TU;COUNT=2",
        )

    def test_anonymous_gets_events(self):
        resp = self.client.get(self.url, {"start": "2026-09-01", "end": "2026-09-30"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()["events"]), 2)

    def test_private_feed_excluded_for_anonymous(self):
        private = make_feed(name="Private", visibility=CalendarFeed.VISIBILITY_MEMBERS)
        make_event(
            private,
            title="Private Event",
            start_datetime=self.start,
            end_datetime=self.start + timedelta(hours=1),
        )
        resp = self.client.get(self.url, {"start": "2026-09-01", "end": "2026-09-30"})
        self.assertEqual(len(resp.json()["events"]), 2)  # only public feed events

    def test_invalid_dates_returns_empty(self):
        resp = self.client.get(self.url, {"start": "bad", "end": "bad"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["events"], [])


class FeedCreateViewTests(TestCase):
    def setUp(self):
        self.url = reverse("calendar_feeds:feed_create")
        self.lead = make_lead_mentor_user()
        self.mentor = make_mentor_user()

    def test_lead_mentor_can_access(self):
        self.client.login(username="lead", password="password123")  # nosec B106
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)

    def test_non_lead_redirected_home(self):
        self.client.login(username="mentor", password="password123")  # nosec B106
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("home"))

    def test_create_feed_with_acls(self):
        self.client.login(username="lead", password="password123")  # nosec B106
        resp = self.client.post(
            self.url,
            {
                "name": "Test",
                "slug": "test",
                "visibility": "members",
                "source_type": "manual",
                "color": "#ff0000",
                "is_active": "on",
                "acls-TOTAL_FORMS": 4,
                "acls-INITIAL_FORMS": 0,
                "acls-MIN_NUM_FORMS": 0,
                "acls-MAX_NUM_FORMS": 1000,
                "acls-0-role": "Mentor",
                "acls-0-can_read": "on",
                "acls-0-can_write": "on",
                "acls-1-role": "Parent",
                "acls-1-can_read": "on",
                "acls-2-role": "Student",
                "acls-2-can_read": "on",
                "acls-3-role": "Alumni",
                "acls-3-can_read": "on",
                "acls-3-can_write": "on",
            },
        )
        self.assertRedirects(resp, reverse("calendar_feeds:feed_list"))
        feed = CalendarFeed.objects.get(slug="test")
        self.assertTrue(feed.is_active)
        acl_roles = set(feed.acls.values_list("role", flat=True))
        self.assertEqual(acl_roles, {"Mentor", "Parent", "Student", "Alumni"})


class FeedUpdateViewTests(TestCase):
    def setUp(self):
        self.feed = make_feed(name="Original Name")
        self.url = reverse("calendar_feeds:feed_edit", args=[self.feed.pk])
        self.lead = make_lead_mentor_user()

    def test_update_feed(self):
        self.client.login(username="lead", password="password123")  # nosec B106
        resp = self.client.post(
            self.url,
            {
                "name": "Updated Name",
                "slug": self.feed.slug,
                "visibility": "members",
                "source_type": "manual",
                "color": "#ff0000",
                "acls-TOTAL_FORMS": 0,
                "acls-INITIAL_FORMS": 0,
                "acls-MIN_NUM_FORMS": 0,
                "acls-MAX_NUM_FORMS": 1000,
            },
        )
        self.assertRedirects(resp, reverse("calendar_feeds:feed_list"))
        self.feed.refresh_from_db()
        self.assertEqual(self.feed.name, "Updated Name")


class FeedDeleteViewTests(TestCase):
    def setUp(self):
        self.feed = make_feed()
        self.url = reverse("calendar_feeds:feed_delete", args=[self.feed.pk])
        self.lead = make_lead_mentor_user()

    def test_delete_feed(self):
        self.client.login(username="lead", password="password123")  # nosec B106
        resp = self.client.post(self.url)
        self.assertRedirects(resp, reverse("calendar_feeds:feed_list"))
        self.assertFalse(CalendarFeed.objects.filter(pk=self.feed.pk).exists())


class EventCreateViewTests(TestCase):
    def setUp(self):
        self.feed = make_feed()
        self.url = reverse("calendar_feeds:event_create", args=[self.feed.pk])
        self.calendar_url = reverse("calendar_feeds:calendar_view")
        self.lead = make_lead_mentor_user()
        self.mentor = make_mentor_user()
        self.parent = make_parent_user()

    def test_lead_can_create(self):
        self.client.login(username="lead", password="password123")  # nosec B106
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)

    def test_readonly_acl_parent_cannot_create(self):
        self.client.login(username="parent", password="password123")  # nosec B106
        resp = self.client.get(self.url)
        self.assertRedirects(resp, self.calendar_url)

    def test_create_event(self):
        now = timezone.now()
        self.client.login(username="lead", password="password123")  # nosec B106
        resp = self.client.post(
            self.url,
            {
                "title": "Test Event",
                "start_datetime": (now + timedelta(days=3)).strftime("%Y-%m-%d %H:%M"),
                "end_datetime": (now + timedelta(days=3, hours=1)).strftime(
                    "%Y-%m-%d %H:%M"
                ),
                "description": "",
                "location": "",
                "all_day": False,
            },
        )
        self.assertRedirects(resp, self.calendar_url)
        self.assertTrue(CalendarEvent.objects.filter(title="Test Event").exists())


class EventUpdateViewTests(TestCase):
    def setUp(self):
        self.feed = make_feed(acls={"Mentor": (True, True)})
        self.event = make_event(self.feed)
        self.url = reverse("calendar_feeds:event_edit", args=[self.event.pk])
        self.calendar_url = reverse("calendar_feeds:calendar_view")
        self.lead = make_lead_mentor_user()

    def test_update_event(self):
        self.client.login(username="lead", password="password123")  # nosec B106
        resp = self.client.post(
            self.url,
            {
                "title": "Updated Event",
                "start_datetime": self.event.start_datetime.strftime("%Y-%m-%d %H:%M"),
                "end_datetime": self.event.end_datetime.strftime("%Y-%m-%d %H:%M"),
                "description": "",
                "location": "",
                "all_day": False,
            },
        )
        self.assertRedirects(resp, self.calendar_url)
        self.event.refresh_from_db()
        self.assertEqual(self.event.title, "Updated Event")


class EventDeleteViewTests(TestCase):
    def setUp(self):
        self.feed = make_feed()
        self.event = make_event(self.feed)
        self.url = reverse("calendar_feeds:event_delete", args=[self.event.pk])
        self.calendar_url = reverse("calendar_feeds:calendar_view")
        self.lead = make_lead_mentor_user()

    def test_delete_event(self):
        self.client.login(username="lead", password="password123")  # nosec B106
        resp = self.client.post(self.url)
        self.assertRedirects(resp, self.calendar_url)
        self.assertFalse(CalendarEvent.objects.filter(pk=self.event.pk).exists())


class EventExceptionCreateViewTests(TestCase):
    def setUp(self):
        self.feed = make_feed()
        self.event = make_event(
            self.feed,
            rrule_ical="FREQ=WEEKLY;BYDAY=TU;COUNT=4",
        )
        self.url = reverse("calendar_feeds:exception_create", args=[self.event.pk])
        self.calendar_url = reverse("calendar_feeds:calendar_view")
        self.lead = make_lead_mentor_user()

    def test_lead_can_access(self):
        self.client.login(username="lead", password="password123")  # nosec B106
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)

    def test_create_deleted_exception(self):
        target = self.event.start_datetime.date() + timedelta(weeks=1)
        self.client.login(username="lead", password="password123")  # nosec B106
        resp = self.client.post(
            self.url,
            {
                "original_date": target.isoformat(),
                "change_type": "deleted",
                "note": "Cancelled",
            },
        )
        self.assertRedirects(resp, self.calendar_url)
        self.assertTrue(
            EventException.objects.filter(
                event=self.event, change_type="deleted"
            ).exists()
        )


class EventExceptionListViewTests(TestCase):
    def setUp(self):
        self.feed = make_feed()
        self.event = make_event(
            self.feed,
            rrule_ical="FREQ=WEEKLY;BYDAY=TU;COUNT=4",
        )
        EventException.objects.create(
            event=self.event,
            original_date=self.event.start_datetime.date() + timedelta(weeks=1),
            change_type=EventException.CHANGE_DELETED,
        )
        self.url = reverse("calendar_feeds:exception_list", args=[self.event.pk])
        self.lead = make_lead_mentor_user()

    def test_exceptions_listed(self):
        self.client.login(username="lead", password="password123")  # nosec B106
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.context["exceptions"]), 1)
