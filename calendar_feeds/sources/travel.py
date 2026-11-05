"""
Travel source connector for calendar feeds.

This module provides functions to sync TravelEvent and TravelDeparture records
to CalendarEvent records when a CalendarFeed with source_type=TRAVEL exists.
"""

import logging
from datetime import datetime, time

from django.db.models import Q
from django.utils import timezone

logger = logging.getLogger(__name__)


def sync_travel_event(travel_event, feed):
    """Sync a single TravelEvent to a CalendarEvent on the given feed."""
    from calendar_feeds.models import CalendarEvent

    if (
        feed.source_type != "travel"
        or feed.organization_id != travel_event.organization_id
        or (feed.program_id and feed.program_id != travel_event.program_id)
        or (
            travel_event.program_id
            and travel_event.program.organization_id != travel_event.organization_id
        )
    ):
        logger.warning(
            "Skipping travel event %s with mismatched organization or feed",
            travel_event.pk,
        )
        return None

    # All-day event covering start_date to end_date
    start_dt = timezone.make_aware(datetime.combine(travel_event.start_date, time.min))
    end_dt = timezone.make_aware(datetime.combine(travel_event.end_date, time.max))

    title = travel_event.name
    location_parts = []
    if travel_event.location_name:
        location_parts.append(travel_event.location_name)
    if travel_event.location_address:
        location_parts.append(travel_event.location_address)
    location = ", ".join(location_parts)

    description_parts = []
    if travel_event.description:
        description_parts.append(travel_event.description)

    # Include departure information if present
    departures = list(
        travel_event.departures.filter(organization_id=travel_event.organization_id)
    )
    if departures:
        dep_lines = ["Departures:"]
        for dep in departures:
            line = f"- {dep.label or 'Departure'}: {dep.leave_date}"
            if dep.leave_time:
                line += f" at {dep.leave_time.strftime('%I:%M %p')}"
            if dep.meeting_location:
                line += f" ({dep.meeting_location})"
            dep_lines.append(line)
        description_parts.append("\n".join(dep_lines))

    description = "\n\n".join(description_parts)

    source_id = str(travel_event.pk)
    defaults = {
        "title": title,
        "description": description,
        "start_datetime": start_dt,
        "end_datetime": end_dt,
        "all_day": True,
        "location": location,
    }
    source_events = CalendarEvent.objects.filter(feed=feed, source_id=source_id)
    if (
        source_events.exists()
        and not source_events.filter(organization_id=feed.organization_id).exists()
    ):
        logger.warning(
            "Skipping travel event %s because its calendar row has a mismatched organization",
            travel_event.pk,
        )
        return None
    cal_event, created = CalendarEvent.objects.update_or_create(
        organization_id=feed.organization_id,
        feed=feed,
        source_id=source_id,
        defaults=defaults,
    )

    action = "created" if created else "updated"
    logger.debug("Travel event %s %s on feed '%s'", travel_event.pk, action, feed.name)
    return cal_event


def remove_travel_event(travel_event, feed=None):
    """Remove CalendarEvent(s) for a deleted TravelEvent."""
    from calendar_feeds.models import CalendarEvent

    if not travel_event.organization_id or (
        travel_event.program_id
        and travel_event.program.organization_id != travel_event.organization_id
    ):
        return

    events = CalendarEvent.objects.filter(
        organization_id=travel_event.organization_id,
        feed__organization_id=travel_event.organization_id,
        source_id=str(travel_event.pk),
        feed__source_type="travel",
    )
    if travel_event.program_id:
        events = events.filter(
            Q(feed__program_id=travel_event.program_id) | Q(feed__program__isnull=True)
        )
    else:
        events = events.filter(feed__program__isnull=True)

    if feed:
        if (
            feed.source_type != "travel"
            or feed.organization_id != travel_event.organization_id
            or feed.program_id not in (None, travel_event.program_id)
        ):
            return
        events = events.filter(feed=feed)

    count, _ = events.delete()
    if count:
        logger.debug(
            "Removed %d calendar event(s) for travel event %s", count, travel_event.pk
        )


def initial_sync(feed):
    """Do a full sync of all TravelEvents for a program to a feed.

    Called when a CalendarFeed with source_type=TRAVEL is first created.
    """
    try:
        from travel.models import TravelEvent
    except ImportError:
        logger.warning("travel app not available; skipping initial sync")
        return 0

    if feed.source_type != "travel" or not feed.organization_id:
        logger.warning("Skipping travel sync for invalid feed '%s'", feed.name)
        return 0

    events = TravelEvent.objects.filter(
        organization_id=feed.organization_id
    ).prefetch_related("departures")
    if feed.program:
        if feed.program.organization_id != feed.organization_id:
            logger.warning(
                "Skipping travel sync for cross-organization program feed '%s'",
                feed.name,
            )
            return 0
        events = events.filter(
            program=feed.program,
            program__organization_id=feed.organization_id,
        )
    else:
        events = events.filter(
            Q(program__isnull=True) | Q(program__organization_id=feed.organization_id)
        )

    count = 0
    for travel_event in events:
        if sync_travel_event(travel_event, feed) is not None:
            count += 1

    return count
