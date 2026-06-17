import importlib
from collections import namedtuple
from unittest.mock import MagicMock

from django.db.utils import ProgrammingError
from django.test import TestCase


class Migration0078FixTest(TestCase):
    def test_fix_uses_introspection_instead_of_pragma(self):
        """Verify that the fixed implementation uses introspection and doesn't call PRAGMA."""
        # Mock schema_editor and connection
        schema_editor = MagicMock()
        connection = MagicMock()
        schema_editor.connection = connection

        # Mock introspection
        FieldInfo = namedtuple("FieldInfo", ["name"])
        connection.introspection.get_table_description.return_value = [
            FieldInfo(name="id"),
            FieldInfo(name="some_other_column"),
        ]

        cursor = MagicMock()
        connection.cursor.return_value = cursor

        # Mock cursor to fail on PRAGMA to ensure it's not used
        def execute_side_effect(sql, params=None):
            if "PRAGMA" in sql:
                raise ProgrammingError("PRAGMA should not be used!")
            return MagicMock()

        cursor.execute.side_effect = execute_side_effect

        migration_module = importlib.import_module(
            "programs.migrations.0078_consolidate_adult_emails"
        )
        add_andrew_columns_if_missing = migration_module.add_andrew_columns_if_missing

        # This should NOT fail
        add_andrew_columns_if_missing(None, schema_editor)

        # Verify introspection was used
        connection.introspection.get_table_description.assert_called()

        # Verify that it tried to add missing columns
        # andrew_id, andrew_email, andrew_id_expiration, andrew_id_sponsor_id should be added
        # since only 'id' and 'some_other_column' are in our mock description
        add_col_calls = [
            call[0][0]
            for call in cursor.execute.call_args_list
            if "ALTER TABLE" in call[0][0]
        ]
        self.assertTrue(any("andrew_id" in sql for sql in add_col_calls))
        self.assertTrue(any("andrew_email" in sql for sql in add_col_calls))
        self.assertTrue(any("andrew_id_expiration" in sql for sql in add_col_calls))
        self.assertTrue(any("andrew_id_sponsor_id" in sql for sql in add_col_calls))
        self.assertEqual(len(add_col_calls), 4)

    def test_fix_idempotency(self):
        """Verify that if columns already exist, no ALTER TABLE calls are made."""
        schema_editor = MagicMock()
        connection = MagicMock()
        schema_editor.connection = connection

        FieldInfo = namedtuple("FieldInfo", ["name"])
        connection.introspection.get_table_description.return_value = [
            FieldInfo(name="id"),
            FieldInfo(name="andrew_id"),
            FieldInfo(name="andrew_email"),
            FieldInfo(name="andrew_id_expiration"),
            FieldInfo(name="andrew_id_sponsor_id"),
        ]

        cursor = MagicMock()
        connection.cursor.return_value = cursor

        migration_module = importlib.import_module(
            "programs.migrations.0078_consolidate_adult_emails"
        )
        add_andrew_columns_if_missing = migration_module.add_andrew_columns_if_missing

        add_andrew_columns_if_missing(None, schema_editor)

        # No ALTER TABLE should be called
        add_col_calls = [
            call[0][0]
            for call in cursor.execute.call_args_list
            if "ALTER TABLE" in call[0][0]
        ]
        self.assertEqual(len(add_col_calls), 0)
