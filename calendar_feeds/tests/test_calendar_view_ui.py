"""Tests for CalendarView UI context and permissions."""

from django.test import TestCase

from calendar_feeds.models import CalendarFeed, UserCalendarToken
from calendar_feeds.tests.factories import (
    make_feed,
    make_lead_mentor_user,
    make_mentor_user,
    make_parent_user,
    make_student_user,
)


class CalendarViewUITests(TestCase):
    def setUp(self):
        self.lead = make_lead_mentor_user()
        self.mentor = make_mentor_user()
        self.parent = make_parent_user()
        self.student_user, self.student = make_student_user()

        self.public_feed = make_feed(
            name="Public Events",
            visibility=CalendarFeed.VISIBILITY_ANONYMOUS,
            source_type=CalendarFeed.SOURCE_MANUAL,
        )
        self.mentor_feed = make_feed(
            name="Mentors Feed",
            visibility=CalendarFeed.VISIBILITY_INVITE_ONLY,
            source_type=CalendarFeed.SOURCE_MANUAL,
            acls={"Mentor": (True, True), "Parent": (False, False)},
        )
        self.outreach_feed = make_feed(
            name="Outreach Events",
            visibility=CalendarFeed.VISIBILITY_MEMBERS,
            source_type=CalendarFeed.SOURCE_OUTREACH,
        )

    def test_anonymous_user_view_context(self):
        resp = self.client.get("/calendar/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(list(resp.context["feeds"]), [self.public_feed])
        self.assertEqual(resp.context["writable_feeds"], [])
        self.assertFalse(resp.context["can_create_feed"])
        self.assertIsNone(resp.context.get("personal_ics_url"))

    def test_mentor_user_view_context_has_writable_feeds_and_token_url(self):
        self.client.login(username="mentor", password="password123")  # nosec B106
        resp = self.client.get("/calendar/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(self.public_feed, resp.context["feeds"])
        self.assertIn(self.mentor_feed, resp.context["feeds"])
        self.assertIn(self.outreach_feed, resp.context["feeds"])

        # Outreach is source-managed, so only manual feeds with can_write are in writable_feeds
        self.assertIn(self.mentor_feed, resp.context["writable_feeds"])
        self.assertNotIn(self.outreach_feed, resp.context["writable_feeds"])

        token = UserCalendarToken.objects.get(user=self.mentor).token
        self.assertIn(str(token), resp.context["personal_ics_url"])

    def test_parent_user_view_context(self):
        self.client.login(username="parent", password="password123")  # nosec B106
        resp = self.client.get("/calendar/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(self.public_feed, resp.context["feeds"])
        self.assertNotIn(self.mentor_feed, resp.context["feeds"])
        self.assertEqual(resp.context["writable_feeds"], [])
