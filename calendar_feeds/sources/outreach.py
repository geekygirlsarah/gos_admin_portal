"""
Outreach source connector for calendar feeds.

This module provides functions to sync OutreachShift records
to CalendarEvent records when a CalendarFeed with source_type=OUTREACH exists.
"""

import logging
from datetime import datetime, time

from django.utils import timezone

logger = logging.getLogger(__name__)


def sync_outreach_shift(shift, feed):
    """Sync a single OutreachShift to a CalendarEvent on the given feed."""
    from .models import CalendarEvent

    event = shift.event

    start_dt = timezone.make_aware(
        datetime.combine(shift.date, shift.start_time or time.min)
    )
    end_dt = timezone.make_aware(
        datetime.combine(shift.date, shift.end_time or time.max)
    )

    title = event.name
    location_parts = []
    if event.location_name:
        location_parts.append(event.location_name)
    if event.location_address:
        location_parts.append(event.location_address)
    location = ", ".join(location_parts)

    description = event.description or ""

    cal_event, created = CalendarEvent.objects.update_or_create(
        feed=feed,
        source_id=str(shift.pk),
        defaults={
            "title": title,
            "description": description,
            "start_datetime": start_dt,
            "end_datetime": end_dt,
            "all_day": False,
            "location": location,
        },
    )

    action = "created" if created else "updated"
    logger.debug("Outreach shift %s %s on feed '%s'", shift.pk, action, feed.name)
    return cal_event


def remove_outreach_shift(shift, feed):
    """Remove a CalendarEvent for a deleted OutreachShift."""
    from .models import CalendarEvent

    count, _ = CalendarEvent.objects.filter(
        feed=feed,
        source_id=str(shift.pk),
    ).delete()
    if count:
        logger.debug(
            "Removed %d calendar event(s) for outreach shift %s", count, shift.pk
        )


def initial_sync(feed):
    """Do a full sync of all OutreachShifts for a program to a feed.

    Called when a CalendarFeed with source_type=OUTREACH is first created.
    """
    try:
        from outreach.models import OutreachShift
    except ImportError:
        logger.warning("outreach app not available; skipping initial sync")
        return 0

    if not feed.program:
        logger.warning("Outreach feed '%s' has no program; skipping sync", feed.name)
        return 0

    shifts = OutreachShift.objects.filter(
        event__program=feed.program,
    ).select_related("event")

    count = 0
    for shift in shifts:
        sync_outreach_shift(shift, feed)
        count += 1

    return count
