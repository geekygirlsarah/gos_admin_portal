"""
Travel source connector for calendar feeds.

This module provides functions to sync TravelEvent and TravelDeparture records
to CalendarEvent records when a CalendarFeed with source_type=TRAVEL exists.
"""

import logging
from datetime import datetime, time

from django.utils import timezone

logger = logging.getLogger(__name__)


def sync_travel_event(travel_event, feed):
    """Sync a single TravelEvent to a CalendarEvent on the given feed."""
    from calendar_feeds.models import CalendarEvent

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
    departures = list(travel_event.departures.all())
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

    cal_event, created = CalendarEvent.objects.update_or_create(
        feed=feed,
        source_id=str(travel_event.pk),
        defaults={
            "title": title,
            "description": description,
            "start_datetime": start_dt,
            "end_datetime": end_dt,
            "all_day": True,
            "location": location,
        },
    )

    action = "created" if created else "updated"
    logger.debug("Travel event %s %s on feed '%s'", travel_event.pk, action, feed.name)
    return cal_event


def remove_travel_event(travel_event, feed=None):
    """Remove CalendarEvent(s) for a deleted TravelEvent."""
    from calendar_feeds.models import CalendarEvent

    filters = {"source_id": str(travel_event.pk), "feed__source_type": "travel"}
    if feed:
        filters["feed"] = feed

    count, _ = CalendarEvent.objects.filter(**filters).delete()
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

    events = TravelEvent.objects.all().prefetch_related("departures")
    if feed.program:
        events = events.filter(program=feed.program)

    count = 0
    for travel_event in events:
        sync_travel_event(travel_event, feed)
        count += 1

    return count
