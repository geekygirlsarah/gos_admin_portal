from django.db import migrations


def convert_event_dates_to_shifts(apps, schema_editor):
    """Convert each event's legacy start/end date/time into its first shift."""
    OutreachEvent = apps.get_model("outreach", "OutreachEvent")
    OutreachShift = apps.get_model("outreach", "OutreachShift")

    for event in OutreachEvent.objects.all():
        OutreachShift.objects.create(
            event=event,
            date=event.start_date,
            start_time=event.start_time,
            end_time=event.end_time,
        )


def revert_shifts_to_event_dates(apps, schema_editor):
    """Copy the first shift's date/time back onto the event, then drop shifts."""
    OutreachEvent = apps.get_model("outreach", "OutreachEvent")

    for event in OutreachEvent.objects.all():
        first_shift = event.shifts.order_by("date", "start_time").first()
        if first_shift:
            event.start_date = first_shift.date
            event.start_time = first_shift.start_time
            last_shift = event.shifts.order_by("date", "start_time").last()
            event.end_date = last_shift.date
            event.end_time = last_shift.end_time
            event.save(
                update_fields=["start_date", "start_time", "end_date", "end_time"]
            )
        event.shifts.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("outreach", "0003_outreachshift"),
    ]

    operations = [
        migrations.RunPython(
            convert_event_dates_to_shifts, revert_shifts_to_event_dates
        ),
    ]
