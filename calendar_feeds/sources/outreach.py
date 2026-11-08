"""
Outreach source connector for calendar feeds.

This module provides functions to sync OutreachShift records
to CalendarEvent records when a CalendarFeed with source_type=OUTREACH exists.
"""

import logging
from datetime import datetime, time

from django.db import transaction
from django.db.models import F
from django.utils import timezone

logger = logging.getLogger(__name__)


def sync_outreach_shift(shift, feed):
    """Sync a single OutreachShift to a CalendarEvent on the given feed."""
    from calendar_feeds.models import CalendarEvent

    event = shift.event
    if (
        feed.source_type != "outreach"
        or feed.organization_id != shift.organization_id
        or event.organization_id != feed.organization_id
        or feed.program_id != event.program_id
        or (event.program_id and event.program.organization_id != feed.organization_id)
    ):
        logger.warning(
            "Skipping outreach shift %s with mismatched organization or feed",
            shift.pk,
        )
        return None

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

    source_id = str(shift.pk)
    defaults = {
        "title": title,
        "description": description,
        "start_datetime": start_dt,
        "end_datetime": end_dt,
        "all_day": False,
        "location": location,
    }
    source_events = CalendarEvent.objects.filter(feed=feed, source_id=source_id)
    if (
        source_events.exists()
        and not source_events.filter(organization_id=feed.organization_id).exists()
    ):
        logger.warning(
            "Skipping outreach shift %s because its calendar row has a mismatched organization",
            shift.pk,
        )
        return None
    cal_event, created = CalendarEvent.objects.update_or_create(
        organization_id=feed.organization_id,
        feed=feed,
        source_id=source_id,
        defaults=defaults,
    )

    action = "created" if created else "updated"
    logger.debug("Outreach shift %s %s on feed '%s'", shift.pk, action, feed.name)
    return cal_event


def remove_outreach_shift(shift, feed):
    """Remove a CalendarEvent for a deleted OutreachShift."""
    from calendar_feeds.models import CalendarEvent, EventException

    event = shift.event
    if (
        feed.source_type != "outreach"
        or feed.organization_id != shift.organization_id
        or event.organization_id != feed.organization_id
        or feed.program_id != event.program_id
        or (event.program_id and event.program.organization_id != feed.organization_id)
    ):
        return

    events = CalendarEvent.objects.filter(
        organization_id=feed.organization_id,
        feed=feed,
        source_id=str(shift.pk),
    )
    with transaction.atomic():
        candidate_ids = list(events.select_for_update().values_list("pk", flat=True))
        list(
            EventException.objects.select_for_update()
            .filter(event_id__in=candidate_ids)
            .values_list("pk", flat=True)
        )
        foreign_exceptions = EventException.objects.filter(
            event_id__in=candidate_ids
        ).exclude(organization_id=F("event__organization_id"))
        safe_events = CalendarEvent.objects.filter(pk__in=candidate_ids).exclude(
            pk__in=foreign_exceptions.values("event_id")
        )
        count, _ = safe_events.delete()
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

    if feed.source_type != "outreach" or not feed.organization_id:
        logger.warning("Skipping outreach sync for invalid feed '%s'", feed.name)
        return 0

    if not feed.program:
        logger.warning("Outreach feed '%s' has no program; skipping sync", feed.name)
        return 0

    if feed.program.organization_id != feed.organization_id:
        logger.warning(
            "Skipping outreach sync for cross-organization program feed '%s'",
            feed.name,
        )
        return 0

    shifts = OutreachShift.objects.filter(
        organization_id=feed.organization_id,
        event__organization_id=feed.organization_id,
        event__program=feed.program,
        event__program__organization_id=feed.organization_id,
    ).select_related("event")

    count = 0
    for shift in shifts:
        if sync_outreach_shift(shift, feed) is not None:
            count += 1

    return count
