import logging

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import CalendarEvent

logger = logging.getLogger(__name__)


@receiver(post_save, sender="outreach.OutreachShift")
def sync_outreach_shift_to_calendar(sender, instance, created, **kwargs):
    """Sync an OutreachShift to a CalendarEvent when the shift is saved."""
    try:
        _do_sync_outreach_shift(instance)
    except Exception:
        logger.debug("Failed to sync outreach shift to calendar", exc_info=True)


@receiver(post_delete, sender="outreach.OutreachShift")
def remove_outreach_shift_from_calendar(sender, instance, **kwargs):
    """Remove the CalendarEvent when an OutreachShift is deleted."""
    try:
        CalendarEvent.objects.filter(
            source_id=str(instance.pk),
            feed__source_type="outreach",
        ).delete()
    except Exception:
        logger.debug("Failed to remove outreach shift calendar event", exc_info=True)


def _do_sync_outreach_shift(shift):
    """Create or update a CalendarEvent for an OutreachShift."""
    from datetime import datetime, time

    from django.utils import timezone

    from .models import CalendarFeed

    # Find or determine the feed
    event = shift.event
    program = event.program if hasattr(event, "program") else None

    feed = CalendarFeed.objects.filter(
        source_type="outreach",
        program=program,
    ).first()

    if not feed:
        # No feed configured for outreach; nothing to sync
        return

    # Build datetime from the shift's date + times
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

    cal_event, was_created = CalendarEvent.objects.update_or_create(
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
