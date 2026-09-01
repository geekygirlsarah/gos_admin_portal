"""Shared test helpers for the outreach app."""

from datetime import datetime

from django.utils import timezone

from outreach.models import OutreachEvent, OutreachShift


def create_outreach_event(*, start_date, start_time, end_time, end_date=None, **kwargs):
    """Create an ``OutreachEvent`` with one or two shifts.

    Convenience wrapper for tests written against the legacy single
    start/end date-time API. Creates a shift on ``start_date`` and, when
    ``end_date`` differs from ``start_date``, a second shift on
    ``end_date`` so the event's derived start/end still match what the
    caller requested.
    """
    event = OutreachEvent.objects.create(**kwargs)
    OutreachShift.objects.create(
        event=event, date=start_date, start_time=start_time, end_time=end_time
    )
    if end_date and end_date != start_date:
        OutreachShift.objects.create(
            event=event, date=end_date, start_time=start_time, end_time=end_time
        )
    return event


def record_full_attendance(signup, shift):
    """Stamp a signup as having attended the whole scheduled shift."""
    signup.checked_in_at = timezone.make_aware(
        datetime.combine(shift.date, shift.start_time)
    )
    signup.checked_out_at = timezone.make_aware(
        datetime.combine(shift.date, shift.end_time)
    )
    signup.save(update_fields=["checked_in_at", "checked_out_at"])
    return signup
