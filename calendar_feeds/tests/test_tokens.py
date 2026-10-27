"""Tests for UserCalendarToken and tokenized ICS feeds."""

import uuid
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from calendar_feeds.models import CalendarFeed, UserCalendarToken
from calendar_feeds.tests.factories import (
    make_event,
    make_feed,
    make_lead_mentor_user,
    make_mentor_user,
    make_parent_user,
    make_student_user,
)


def _within_feed_window(days_from_today=7, hour=10):
    day = timezone.localdate() + timedelta(days=days_from_today)
    return timezone.make_aware(
        timezone.datetime.combine(day, timezone.datetime.min.time()).replace(hour=hour),
        timezone.UTC,
    )


class UserCalendarTokenModelTests(TestCase):
    def setUp(self):
        self.mentor = make_mentor_user()

    def test_for_user_creates_token(self):
        token_obj = UserCalendarToken.for_user(self.mentor)
        self.assertIsNotNone(token_obj.token)
        self.assertEqual(token_obj.user, self.mentor)

    def test_for_user_idempotent(self):
        t1 = UserCalendarToken.for_user(self.mentor)
        t2 = UserCalendarToken.for_user(self.mentor)
        self.assertEqual(t1.pk, t2.pk)
        self.assertEqual(t1.token, t2.token)

    def test_regenerate_changes_token(self):
        token_obj = UserCalendarToken.for_user(self.mentor)
        old_token = token_obj.token
        new_token = token_obj.regenerate()
        self.assertNotEqual(old_token, new_token)
        token_obj.refresh_from_db()
        self.assertEqual(token_obj.token, new_token)


class PersonalICSFeedViewTests(TestCase):
    def setUp(self):
        self.lead = make_lead_mentor_user()
        self.mentor = make_mentor_user()
        self.parent = make_parent_user()
        self.student_user, self.student = make_student_user()

        self.public_feed = make_feed(
            name="Public Feed",
            visibility=CalendarFeed.VISIBILITY_ANONYMOUS,
            slug="public",
        )
        self.mentor_feed = make_feed(
            name="Mentors Feed",
            visibility=CalendarFeed.VISIBILITY_INVITE_ONLY,
            slug="mentors",
            acls={"Mentor": (True, True)},
        )
        self.student_feed = make_feed(
            name="Students Feed",
            visibility=CalendarFeed.VISIBILITY_INVITE_ONLY,
            slug="students",
            acls={"Student": (True, False)},
        )

        self.start = _within_feed_window(days_from_today=5)
        make_event(self.public_feed, title="Public Workshop", start_datetime=self.start)
        make_event(
            self.mentor_feed, title="Mentor Strategy Meeting", start_datetime=self.start
        )
        make_event(
            self.student_feed, title="Student Kickoff", start_datetime=self.start
        )

    def test_mentor_personal_feed_contains_public_and_mentor_events_only(self):
        token = UserCalendarToken.for_user(self.mentor).token
        url = f"/calendar/my/{token}/feed.ics"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/calendar", resp["Content-Type"])
        body = resp.content.decode("utf-8")
        self.assertIn("Public Workshop", body)
        self.assertIn("Mentor Strategy Meeting", body)
        self.assertNotIn("Student Kickoff", body)

    def test_student_personal_feed_contains_public_and_student_events_only(self):
        token = UserCalendarToken.for_user(self.student_user).token
        url = f"/calendar/my/{token}/feed.ics"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode("utf-8")
        self.assertIn("Public Workshop", body)
        self.assertIn("Student Kickoff", body)
        self.assertNotIn("Mentor Strategy Meeting", body)

    def test_invalid_token_returns_404(self):
        bad_token = uuid.uuid4()
        url = f"/calendar/my/{bad_token}/feed.ics"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 404)


class FeedICSViewTokenAuthTests(TestCase):
    def setUp(self):
        self.mentor = make_mentor_user()
        self.parent = make_parent_user()

        self.mentor_feed = make_feed(
            name="Private Mentors Feed",
            visibility=CalendarFeed.VISIBILITY_INVITE_ONLY,
            slug="private-mentors",
            acls={"Mentor": (True, True)},
        )
        self.start = _within_feed_window(days_from_today=5)
        make_event(
            self.mentor_feed, title="Private Mentor Sync", start_datetime=self.start
        )

    def test_unauthenticated_with_valid_mentor_token_param(self):
        token = UserCalendarToken.for_user(self.mentor).token
        url = f"/calendar/{self.mentor_feed.slug}/feed.ics?token={token}"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Private Mentor Sync", resp.content.decode("utf-8"))

    def test_unauthenticated_with_valid_mentor_token_path(self):
        token = UserCalendarToken.for_user(self.mentor).token
        url = f"/calendar/{self.mentor_feed.slug}/{token}/feed.ics"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Private Mentor Sync", resp.content.decode("utf-8"))

    def test_unauthenticated_with_parent_token_denied_for_mentor_feed(self):
        token = UserCalendarToken.for_user(self.parent).token
        url = f"/calendar/{self.mentor_feed.slug}/feed.ics?token={token}"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 403)

    def test_unauthenticated_with_invalid_token_denied(self):
        bad_token = uuid.uuid4()
        url = f"/calendar/{self.mentor_feed.slug}/feed.ics?token={bad_token}"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 401)


class RegenerateCalendarTokenViewTests(TestCase):
    def setUp(self):
        self.mentor = make_mentor_user()

    def test_anonymous_redirected_to_login(self):
        resp = self.client.post("/calendar/my/regenerate-token/")
        self.assertEqual(resp.status_code, 302)

    def test_logged_in_user_regenerates_token(self):
        self.client.login(username="mentor", password="password123")  # nosec B106
        token_obj = UserCalendarToken.for_user(self.mentor)
        old_token = token_obj.token
        resp = self.client.post("/calendar/my/regenerate-token/")
        self.assertEqual(resp.status_code, 302)
        token_obj.refresh_from_db()
        self.assertNotEqual(token_obj.token, old_token)
