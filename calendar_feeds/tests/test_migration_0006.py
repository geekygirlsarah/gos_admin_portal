import importlib
from unittest.mock import MagicMock

from django.test import TestCase


class Migration0006NormalizeRruleUntilTest(TestCase):
    def test_normalize_existing_rrules(self):
        apps = MagicMock()
        calendar_event_model = MagicMock()
        apps.get_model.return_value = calendar_event_model

        mock_event_1 = MagicMock()
        mock_event_1.rrule_ical = "FREQ=WEEKLY;UNTIL=20261029"

        mock_event_2 = MagicMock()
        mock_event_2.rrule_ical = "FREQ=WEEKLY;UNTIL=20261029T235959Z"

        mock_event_3 = MagicMock()
        mock_event_3.rrule_ical = "FREQ=DAILY;UNTIL=20261029T120000"

        calendar_event_model.objects.filter.return_value = [
            mock_event_1,
            mock_event_2,
            mock_event_3,
        ]

        migration_module = importlib.import_module(
            "calendar_feeds.migrations.0006_normalize_rrule_until"
        )
        migration_fn = migration_module.normalize_existing_rrules

        migration_fn(apps, None)

        apps.get_model.assert_called_once_with("calendar_feeds", "CalendarEvent")
        calendar_event_model.objects.filter.assert_called_once_with(
            rrule_ical__icontains="UNTIL="
        )

        self.assertEqual(mock_event_1.rrule_ical, "FREQ=WEEKLY;UNTIL=20261029T235959Z")
        mock_event_1.save.assert_called_once_with(update_fields=["rrule_ical"])

        self.assertEqual(mock_event_2.rrule_ical, "FREQ=WEEKLY;UNTIL=20261029T235959Z")
        mock_event_2.save.assert_not_called()

        self.assertEqual(mock_event_3.rrule_ical, "FREQ=DAILY;UNTIL=20261029T120000Z")
        mock_event_3.save.assert_called_once_with(update_fields=["rrule_ical"])
