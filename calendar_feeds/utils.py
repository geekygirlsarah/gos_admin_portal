import logging
from datetime import time, timedelta

from django.utils import timezone

logger = logging.getLogger(__name__)


def expand_events_for_range(feed, start_date, end_date):
    """Expand recurring events and apply exceptions for a date range.

    Returns a list of dicts describing concrete event instances for the given
    range, including recurring event instances and excluding/moving dates per
    EventException rules.
    """
    events = feed.events.order_by("start_datetime")

    expanded = []
    for event in events:
        if event.is_recurring:
            expanded.extend(_expand_recurring(event, start_date, end_date))
        else:
            event_date = event.start_datetime.date()
            if start_date <= event_date <= end_date:
                expanded.append(_make_instance(event, event_date))

    expanded.sort(key=lambda e: (e["date"], e["start_time"] or time.min))
    return expanded


def _expand_recurring(event, start_date, end_date):
    """Expand a single recurring event into concrete instances."""
    instances = []

    if not event.rrule_ical:
        return instances

    # Include a small buffer beyond the requested end so timezone/DST
    # offsets at the boundary don't drop legitimate occurrences. Force UTC:
    # DateTimeFields are stored in UTC under USE_TZ, so the range bounds and
    # the RRULE's dtstart are comparable.
    range_start = timezone.make_aware(
        timezone.datetime.combine(start_date, timezone.datetime.min.time()),
        timezone.UTC,
    )
    range_end = timezone.make_aware(
        timezone.datetime.combine(
            end_date + timedelta(days=1), timezone.datetime.max.time()
        ),
        timezone.UTC,
    )

    try:
        from dateutil.rrule import rrulestr

        rrule = rrulestr(event.rrule_ical, dtstart=event.start_datetime)
        occurrence_iter = rrule.between(range_start, range_end, inc=True)

        for occurrence in occurrence_iter:
            target_date = occurrence.date()

            # Skip manually excluded dates (exdate_ical)
            if target_date in event.exdate_list:
                continue

            exc = event.get_exception_on_date(target_date)
            if exc:
                if exc.change_type == exc.CHANGE_DELETED:
                    continue
                if exc.change_type == exc.CHANGE_MOVED:
                    instances.append(
                        _make_instance(
                            event,
                            exc.new_start_datetime.date(),
                            start_override=exc.new_start_datetime,
                            end_override=exc.new_end_datetime,
                        )
                    )
                    continue
                if exc.change_type == exc.CHANGE_CHANGED:
                    instances.append(
                        _make_instance(
                            event,
                            target_date,
                            title_override=exc.new_title or None,
                            location_override=exc.new_location or None,
                            description_override=exc.new_description or None,
                        )
                    )
                    continue

            instances.append(_make_instance(event, target_date))

    except Exception:
        logger.exception("Error expanding recurring event %s", event.pk)

    return instances


def _make_instance(
    event,
    target_date,
    start_override=None,
    end_override=None,
    title_override=None,
    location_override=None,
    description_override=None,
):
    """Create a dict representing one instance of a calendar event."""
    if start_override:
        start_dt = start_override
    elif event.all_day:
        start_dt = timezone.make_aware(
            timezone.datetime.combine(target_date, timezone.datetime.min.time())
        )
    else:
        start_dt = event.start_datetime.replace(
            year=target_date.year, month=target_date.month, day=target_date.day
        )

    if end_override:
        end_dt = end_override
    elif event.all_day:
        end_dt = timezone.make_aware(
            timezone.datetime.combine(target_date, timezone.datetime.max.time())
        )
    else:
        end_dt = event.end_datetime.replace(
            year=target_date.year, month=target_date.month, day=target_date.day
        )

    return {
        "event": event,
        "pk": event.pk,
        "title": title_override or event.title,
        "description": description_override or event.description,
        "location": location_override or event.location,
        "start_datetime": start_dt,
        "end_datetime": end_dt,
        "start_time": start_dt.time() if not event.all_day else None,
        "end_time": end_dt.time() if not event.all_day else None,
        "date": target_date,
        "all_day": event.all_day,
        "color": event.feed.color,
        "feed_name": event.feed.name,
        "feed_slug": event.feed.slug,
        "uid": event.uid,
    }
