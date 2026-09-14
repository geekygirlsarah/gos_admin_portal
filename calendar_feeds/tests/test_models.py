"""Tests for CalendarFeed / CalendarFeedACL / CalendarEvent / EventException."""

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from calendar_feeds.models import CalendarEvent, CalendarFeed, CalendarFeedACL
from calendar_feeds.tests.factories import (
    make_feed,
    make_lead_mentor_user,
    make_mentor_user,
    make_parent_user,
    make_student_user,
)


class CalendarFeedSlugTests(TestCase):
    def test_slug_auto_generated_from_name(self):
        feed = CalendarFeed.objects.create(name="Student Leadership")
        self.assertEqual(feed.slug, "student-leadership")

    def test_slug_uniqueness_gets_suffix(self):
        CalendarFeed.objects.create(name="Public Calendar")
        second = CalendarFeed.objects.create(name="Public Calendar")
        self.assertEqual(second.slug, "public-calendar-1")

    def test_explicit_slug_respected(self):
        feed = CalendarFeed.objects.create(name="Whatever", slug="custom-slug")
        self.assertEqual(feed.slug, "custom-slug")


class CalendarFeedReadAccessTests(TestCase):
    def setUp(self):
        self.lead = make_lead_mentor_user()
        self.mentor = make_mentor_user()
        self.parent = make_parent_user()
        self.student_user, self.student = make_student_user()

    def test_public_feed_readable_by_anonymous(self):
        from django.contrib.auth.models import AnonymousUser

        feed = make_feed(visibility=CalendarFeed.VISIBILITY_ANONYMOUS)
        self.assertTrue(feed.user_can_read(AnonymousUser()))

    def test_members_feed_not_readable_by_anonymous(self):
        from django.contrib.auth.models import AnonymousUser

        feed = make_feed(visibility=CalendarFeed.VISIBILITY_MEMBERS)
        self.assertFalse(feed.user_can_read(AnonymousUser()))

    def test_lead_mentor_bypasses_acl(self):
        feed = make_feed(visibility=CalendarFeed.VISIBILITY_INVITE_ONLY, acls={})
        self.assertTrue(feed.user_can_read(self.lead))

    def test_mentor_with_read_acl_can_read(self):
        feed = make_feed(acls={"Mentor": (True, True)})
        self.assertTrue(feed.user_can_read(self.mentor))

    def test_role_without_acl_cannot_read(self):
        feed = make_feed(acls={"Mentor": (True, True)})
        self.assertFalse(feed.user_can_read(self.parent))

    def test_acl_can_read_false_blocks_role(self):
        feed = make_feed(acls={"Parent": (False, False)})
        self.assertFalse(feed.user_can_read(self.parent))

    def test_student_with_read_acl_can_read(self):
        feed = make_feed(acls={"Student": (True, False)})
        self.assertTrue(feed.user_can_read(self.student_user))


class CalendarFeedWriteAccessTests(TestCase):
    def setUp(self):
        self.lead = make_lead_mentor_user()
        self.mentor = make_mentor_user()
        self.parent = make_parent_user()

    def test_lead_mentor_can_always_write_manual_feed(self):
        feed = make_feed(acls={})
        self.assertTrue(feed.user_can_write(self.lead))

    def test_source_managed_feed_read_only_for_everyone(self):
        feed = make_feed(
            source_type=CalendarFeed.SOURCE_OUTREACH, acls={"Mentor": (True, True)}
        )
        self.assertFalse(feed.user_can_write(self.lead))
        self.assertFalse(feed.user_can_write(self.mentor))

    def test_mentor_with_write_acl_can_write(self):
        feed = make_feed(acls={"Mentor": (True, True)})
        self.assertTrue(feed.user_can_write(self.mentor))

    def test_parent_with_read_only_acl_cannot_write(self):
        feed = make_feed(acls={"Parent": (True, False)})
        self.assertFalse(feed.user_can_write(self.parent))

    def test_anonymous_cannot_write(self):
        from django.contrib.auth.models import AnonymousUser

        feed = make_feed(visibility=CalendarFeed.VISIBILITY_ANONYMOUS)
        self.assertFalse(feed.user_can_write(AnonymousUser()))


class CalendarFeedACLConstraintTests(TestCase):
    def test_unique_together_feed_role(self):
        feed = make_feed()
        CalendarFeedACL.objects.create(feed=feed, role="Mentor")
        with self.assertRaises(Exception):
            CalendarFeedACL.objects.create(feed=feed, role="Mentor")


class CalendarEventTests(TestCase):
    def test_uid_auto_generated(self):
        feed = make_feed()
        event = CalendarEvent.objects.create(
            feed=feed,
            title="Standup",
            start_datetime=timezone.now(),
            end_datetime=timezone.now() + timedelta(hours=1),
        )
        self.assertTrue(event.uid)

    def test_is_recurring_false_for_one_off(self):
        feed = make_feed()
        event = CalendarEvent.objects.create(
            feed=feed,
            title="One off",
            start_datetime=timezone.now(),
            end_datetime=timezone.now() + timedelta(hours=1),
        )
        self.assertFalse(event.is_recurring)

    def test_is_recurring_true_with_rrule(self):
        feed = make_feed()
        event = CalendarEvent.objects.create(
            feed=feed,
            title="Weekly",
            start_datetime=timezone.now(),
            end_datetime=timezone.now() + timedelta(hours=1),
            rrule_ical="FREQ=WEEKLY;BYDAY=TU",
        )
        self.assertTrue(event.is_recurring)

    def test_clean_rejects_end_before_start(self):
        feed = make_feed()
        now = timezone.now()
        event = CalendarEvent(
            feed=feed,
            title="Bad",
            start_datetime=now,
            end_datetime=now - timedelta(hours=1),
        )
        with self.assertRaises(ValidationError):
            event.full_clean()

    def test_exdate_list_parses_dates(self):
        feed = make_feed()
        event = CalendarEvent.objects.create(
            feed=feed,
            title="Weekly",
            start_datetime=timezone.now(),
            end_datetime=timezone.now() + timedelta(hours=1),
            exdate_ical="2026-09-01, 2026-09-08 ,bad-date",
        )
        self.assertEqual(len(event.exdate_list), 2)

    def test_is_past(self):
        feed = make_feed()
        event = CalendarEvent.objects.create(
            feed=feed,
            title="Past",
            start_datetime=timezone.now() - timedelta(days=2),
            end_datetime=timezone.now() - timedelta(days=1),
        )
        self.assertTrue(event.is_past)


class EventExceptionValidationTests(TestCase):
    def setUp(self):
        self.feed = make_feed()
        self.event = CalendarEvent.objects.create(
            feed=self.feed,
            title="Weekly",
            start_datetime=timezone.now(),
            end_datetime=timezone.now() + timedelta(hours=1),
            rrule_ical="FREQ=WEEKLY;BYDAY=TU",
        )

    def test_moved_requires_new_times(self):
        from calendar_feeds.models import EventException

        exc = EventException(
            event=self.event,
            original_date=timezone.localdate(),
            change_type=EventException.CHANGE_MOVED,
        )
        with self.assertRaises(ValidationError):
            exc.full_clean()

    def test_moved_rejects_end_before_start(self):
        from calendar_feeds.models import EventException

        now = timezone.now()
        exc = EventException(
            event=self.event,
            original_date=timezone.localdate(),
            change_type=EventException.CHANGE_MOVED,
            new_start_datetime=now,
            new_end_datetime=now - timedelta(hours=1),
        )
        with self.assertRaises(ValidationError):
            exc.full_clean()
