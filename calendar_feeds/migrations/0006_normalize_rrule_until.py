import re

from django.db import migrations


def _normalize_rrule_until(rrule_str):
    if not rrule_str or "until=" not in rrule_str.lower():
        return rrule_str
    rrule_str = re.sub(
        r"(?i)\bUNTIL=(\d{8})(?!T)",
        r"UNTIL=\1T235959Z",
        rrule_str,
    )
    rrule_str = re.sub(
        r"(?i)\bUNTIL=(\d{8}T\d{6})(?!Z)",
        r"UNTIL=\1Z",
        rrule_str,
    )
    return rrule_str


def normalize_existing_rrules(apps, schema_editor):
    CalendarEvent = apps.get_model("calendar_feeds", "CalendarEvent")
    for event in CalendarEvent.objects.filter(rrule_ical__icontains="UNTIL="):
        normalized = _normalize_rrule_until(event.rrule_ical)
        if normalized != event.rrule_ical:
            event.rrule_ical = normalized
            event.save(update_fields=["rrule_ical"])


class Migration(migrations.Migration):

    dependencies = [
        ("calendar_feeds", "0005_alter_calendarfeed_visibility"),
    ]

    operations = [
        migrations.RunPython(
            normalize_existing_rrules,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
