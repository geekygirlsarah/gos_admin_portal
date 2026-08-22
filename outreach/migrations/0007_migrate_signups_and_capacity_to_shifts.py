from django.db import migrations


def copy_capacity_and_signups_to_shifts(apps, schema_editor):
    """Copy each event's champion/helper capacity onto its shifts, and
    point each existing signup at that event's first shift (the closest
    equivalent now that students sign up per-shift instead of per-event).
    """
    OutreachEvent = apps.get_model("outreach", "OutreachEvent")
    OutreachSignup = apps.get_model("outreach", "OutreachSignup")

    for event in OutreachEvent.objects.all():
        event.shifts.all().update(
            max_champions=event.max_champions, max_helpers=event.max_helpers
        )

    for signup in OutreachSignup.objects.select_related("event"):
        first_shift = signup.event.shifts.order_by("date", "start_time").first()
        if first_shift:
            signup.shift = first_shift
            signup.save(update_fields=["shift"])


def copy_capacity_and_signups_to_events(apps, schema_editor):
    """Reverse: copy each shift's capacity back onto its event (using the
    first shift), and point each signup back at its shift's event.
    """
    OutreachEvent = apps.get_model("outreach", "OutreachEvent")
    OutreachSignup = apps.get_model("outreach", "OutreachSignup")

    for event in OutreachEvent.objects.all():
        first_shift = event.shifts.order_by("date", "start_time").first()
        if first_shift:
            event.max_champions = first_shift.max_champions
            event.max_helpers = first_shift.max_helpers
            event.save(update_fields=["max_champions", "max_helpers"])

    for signup in OutreachSignup.objects.select_related("shift"):
        if signup.shift:
            signup.event = signup.shift.event
            signup.save(update_fields=["event"])


class Migration(migrations.Migration):

    dependencies = [
        ("outreach", "0006_add_shift_capacity_and_signup_shift"),
    ]

    operations = [
        migrations.RunPython(
            copy_capacity_and_signups_to_shifts,
            copy_capacity_and_signups_to_events,
        ),
    ]
