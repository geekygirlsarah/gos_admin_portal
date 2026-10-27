import importlib
from collections import namedtuple
from unittest.mock import MagicMock

from django.test import TestCase


class Migration0003DropSubscriptionTokenTest(TestCase):
    def test_drops_subscription_token_when_present(self):
        """Verify that ALTER TABLE DROP COLUMN is executed when subscription_token exists."""
        schema_editor = MagicMock()
        connection = MagicMock()
        schema_editor.connection = connection

        connection.introspection.table_names.return_value = [
            "calendar_feeds_calendarfeed"
        ]

        FieldInfo = namedtuple("FieldInfo", ["name"])
        connection.introspection.get_table_description.return_value = [
            FieldInfo(name="id"),
            FieldInfo(name="name"),
            FieldInfo(name="slug"),
            FieldInfo(name="subscription_token"),
        ]
        connection.introspection.get_constraints.return_value = {
            "calendar_feeds_calendarfeed_subscription_token_idx": {
                "columns": ["subscription_token"],
                "index": True,
            }
        }

        cursor = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor

        migration_module = importlib.import_module(
            "calendar_feeds.migrations.0003_drop_subscription_token"
        )
        drop_fn = migration_module.drop_subscription_token_if_exists

        drop_fn(None, schema_editor)

        connection.introspection.get_table_description.assert_called()
        drop_index_calls = [
            call[0][0]
            for call in cursor.execute.call_args_list
            if "DROP INDEX IF EXISTS calendar_feeds_calendarfeed_subscription_token_idx"
            in call[0][0]
        ]
        drop_calls = [
            call[0][0]
            for call in cursor.execute.call_args_list
            if "ALTER TABLE calendar_feeds_calendarfeed DROP COLUMN subscription_token"
            in call[0][0]
        ]
        self.assertEqual(len(drop_index_calls), 1)
        self.assertEqual(len(drop_calls), 1)

    def test_idempotent_when_subscription_token_absent(self):
        """Verify no ALTER TABLE call is made if subscription_token is already gone."""
        schema_editor = MagicMock()
        connection = MagicMock()
        schema_editor.connection = connection

        connection.introspection.table_names.return_value = [
            "calendar_feeds_calendarfeed"
        ]

        FieldInfo = namedtuple("FieldInfo", ["name"])
        connection.introspection.get_table_description.return_value = [
            FieldInfo(name="id"),
            FieldInfo(name="name"),
            FieldInfo(name="slug"),
        ]

        cursor = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor

        migration_module = importlib.import_module(
            "calendar_feeds.migrations.0003_drop_subscription_token"
        )
        drop_fn = migration_module.drop_subscription_token_if_exists

        drop_fn(None, schema_editor)

        drop_calls = [
            call[0][0]
            for call in cursor.execute.call_args_list
            if "ALTER TABLE" in call[0][0]
        ]
        self.assertEqual(len(drop_calls), 0)

    def test_noop_when_table_does_not_exist(self):
        """Verify no error occurs if the table does not exist."""
        schema_editor = MagicMock()
        connection = MagicMock()
        schema_editor.connection = connection

        connection.introspection.table_names.return_value = []

        cursor = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor

        migration_module = importlib.import_module(
            "calendar_feeds.migrations.0003_drop_subscription_token"
        )
        drop_fn = migration_module.drop_subscription_token_if_exists

        drop_fn(None, schema_editor)

        self.assertEqual(len(cursor.execute.call_args_list), 0)
