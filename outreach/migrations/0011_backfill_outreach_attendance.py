"""Backfill check-in/out timestamps for past-shift signups.

Before the check-in/out feature existed, attendance hours were implicitly
"scheduled hours for any past shift". To keep historical totals stable we
stamp existing signups on already-ended shifts with their scheduled
start/end datetimes, so they count as finalized (actual == scheduled).
"""

from datetime import datetime

from django.db import migrations
from django.utils import timezone


def backfill_past_attendance(apps, schema_editor):
    OutreachShift = apps.get_model("outreach", "OutreachShift")
    OutreachSignup = apps.get_model("outreach", "OutreachSignup")

    now = timezone.now()
    updated = 0
    for shift in OutreachShift.objects.all().iterator():
        if _is_past(shift, now):
            start_dt = timezone.make_aware(
                datetime.combine(shift.date, shift.start_time)
            )
            end_dt = timezone.make_aware(datetime.combine(shift.date, shift.end_time))
            updated += OutreachSignup.objects.filter(
                shift=shift, checked_in_at__isnull=True
            ).update(checked_in_at=start_dt, checked_out_at=end_dt)


def _is_past(shift, now):
    end_dt = datetime.combine(shift.date, shift.end_time)
    if timezone.is_naive(end_dt):
        end_dt = timezone.make_aware(end_dt)
    return end_dt < now


def noop(apps, schema_editor):
    """Reverse is intentionally a no-op (restoring nothing)."""


class Migration(migrations.Migration):

    dependencies = [
        ("outreach", "0010_outreachsignup_checked_in_at_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill_past_attendance, noop),
    ]
