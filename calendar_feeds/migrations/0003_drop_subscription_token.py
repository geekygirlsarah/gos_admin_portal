# Hand-written migration to idempotently drop the obsolete subscription_token
# column from calendar_feeds_calendarfeed if present from earlier local dev runs.

from django.db import migrations


def drop_subscription_token_if_exists(apps, schema_editor):
    """Drop legacy subscription_token column from calendar_feeds_calendarfeed if present.

    An earlier local development migration added this column before tokenized access
    was redesigned to use UserCalendarToken. This cleans up the column idempotently
    across SQLite and PostgreSQL.
    """
    db = schema_editor.connection
    with db.cursor() as cursor:
        table_names = db.introspection.table_names(cursor)
        if "calendar_feeds_calendarfeed" not in table_names:
            return
        cols = [
            column.name
            for column in db.introspection.get_table_description(
                cursor, "calendar_feeds_calendarfeed"
            )
        ]
        if "subscription_token" not in cols:
            return

        constraints = db.introspection.get_constraints(
            cursor, "calendar_feeds_calendarfeed"
        )
        for name, details in constraints.items():
            if "subscription_token" in details.get("columns", []):
                if details.get("index") or details.get("unique"):
                    try:
                        cursor.execute(f"DROP INDEX IF EXISTS {name}")
                    except Exception:  # nosec B110
                        pass

        cursor.execute(
            "ALTER TABLE calendar_feeds_calendarfeed DROP COLUMN subscription_token"
        )


class Migration(migrations.Migration):

    dependencies = [
        ("calendar_feeds", "0002_usercalendartoken"),
    ]

    operations = [
        migrations.RunPython(
            drop_subscription_token_if_exists,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
