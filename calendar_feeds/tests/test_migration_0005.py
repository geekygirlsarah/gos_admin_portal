import importlib
from unittest.mock import MagicMock

from django.test import TestCase


class Migration0005AlterCalendarFeedVisibilityTest(TestCase):
    def test_migrate_invite_only_to_members(self):
        apps = MagicMock()
        calendar_feed_model = MagicMock()
        apps.get_model.return_value = calendar_feed_model

        migration_module = importlib.import_module(
            "calendar_feeds.migrations.0005_alter_calendarfeed_visibility"
        )
        migration_fn = migration_module.migrate_invite_only_to_members

        migration_fn(apps, None)

        apps.get_model.assert_called_once_with("calendar_feeds", "CalendarFeed")
        calendar_feed_model.objects.filter.assert_called_once_with(
            visibility="invite_only"
        )
        calendar_feed_model.objects.filter.return_value.update.assert_called_once_with(
            visibility="members"
        )
