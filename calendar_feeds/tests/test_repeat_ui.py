from datetime import date, datetime

from django.template.loader import render_to_string
from django.test import TestCase
from django.utils import timezone

from calendar_feeds.forms import CalendarEventForm
from calendar_feeds.models import CalendarEvent, EventException
from calendar_feeds.tests.factories import make_feed


class RepeatUITests(TestCase):
    def setUp(self):
        self.event = CalendarEvent.objects.create(
            feed=make_feed(),
            title="Meeting",
            start_datetime=timezone.make_aware(datetime(2026, 10, 1, 18)),
            end_datetime=timezone.make_aware(datetime(2026, 10, 1, 19)),
            rrule_ical="FREQ=MONTHLY;BYDAY=1MO;COUNT=4",
            exdate_ical="2026-11-02,2026-12-07",
        )
        self.data = {
            "title": "Updated meeting",
            "start_datetime": "2026-10-01T18:00",
            "end_datetime": "2026-10-01T19:00",
            "recurrence_type": "custom",
            "rrule_ical": self.event.rrule_ical,
        }

    def test_complex_rules_use_advanced_editor(self):
        for rule in (
            "FREQ=MONTHLY;BYDAY=1MO;COUNT=4",
            "FREQ=WEEKLY;WKST=SU",
            "FREQ=DAILY;UNTIL=20261215T180000Z",
        ):
            self.event.rrule_ical = rule
            self.assertEqual(
                CalendarEventForm(instance=self.event).initial["recurrence_type"],
                "custom",
            )

    def test_edit_preserves_rule_and_exclusions(self):
        form = CalendarEventForm(data=self.data, instance=self.event)
        self.assertTrue(form.is_valid(), form.errors)
        event = form.save()
        self.assertEqual(event.rrule_ical, "FREQ=MONTHLY;BYDAY=1MO;COUNT=4")
        self.assertEqual(event.exdate_ical, "2026-11-02,2026-12-07")

    def test_empty_custom_rule_is_rejected(self):
        self.data["rrule_ical"] = ""
        form = CalendarEventForm(data=self.data, instance=self.event)
        self.assertFalse(form.is_valid())
        self.assertIn("rrule_ical", form.errors)

    def test_schedule_change_retains_individual_changes(self):
        exception = EventException.objects.create(
            organization=self.event.organization,
            event=self.event,
            original_date=date(2026, 11, 2),
            change_type=EventException.CHANGE_CHANGED,
            new_title="Special meeting",
        )
        self.data.update(recurrence_type="weekly", repeat_end_type="never")
        form = CalendarEventForm(data=self.data, instance=self.event)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        exception.refresh_from_db()
        self.assertEqual(exception.new_title, "Special meeting")
        self.assertEqual(exception.original_date, date(2026, 11, 2))

    def test_explicitly_clearing_exclusions(self):
        self.data["exdate_ical"] = ""
        form = CalendarEventForm(data=self.data, instance=self.event)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().exdate_ical, "")

    def test_template_explains_individual_changes_and_advanced_fields(self):
        html = render_to_string(
            "calendar_feeds/event_form.html",
            {
                "form": CalendarEventForm(instance=self.event),
                "event": self.event,
                "feed": self.event.feed,
            },
        )
        self.assertIn("Changes to individual dates are retained", html)
        self.assertIn("Advanced: excluded dates", html)
        self.assertIn("Switching to another repeat option replaces it", html)
