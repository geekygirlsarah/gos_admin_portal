import logging

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

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
        from .models import CalendarFeed
        from .sources.outreach import remove_outreach_shift

        event = instance.event
        if instance.organization_id != event.organization_id or (
            event.program_id and event.program.organization_id != event.organization_id
        ):
            return

        feeds = CalendarFeed.objects.filter(
            organization_id=instance.organization_id,
            source_type="outreach",
            program_id=event.program_id,
        )
        for feed in feeds:
            remove_outreach_shift(instance, feed)
    except Exception:
        logger.debug("Failed to remove outreach shift calendar event", exc_info=True)


@receiver(post_save, sender="travel.TravelEvent")
def sync_travel_event_to_calendar(sender, instance, created, **kwargs):
    """Sync a TravelEvent to a CalendarEvent when the trip is saved."""
    try:
        from .sources.travel import sync_travel_event

        feed = _travel_feed_for_event(instance)

        if feed:
            sync_travel_event(instance, feed)
    except Exception:
        logger.debug("Failed to sync travel event to calendar", exc_info=True)


@receiver(post_delete, sender="travel.TravelEvent")
def remove_travel_event_from_calendar(sender, instance, **kwargs):
    """Remove the CalendarEvent when a TravelEvent is deleted."""
    try:
        from .sources.travel import remove_travel_event

        remove_travel_event(instance)
    except Exception:
        logger.debug("Failed to remove travel event from calendar", exc_info=True)


@receiver(post_save, sender="travel.TravelDeparture")
def sync_travel_departure_to_calendar(sender, instance, created, **kwargs):
    """Update the parent TravelEvent's CalendarEvent when a departure is saved."""
    try:
        from .sources.travel import sync_travel_event

        event = instance.event
        if instance.organization_id != event.organization_id:
            return
        feed = _travel_feed_for_event(event)

        if feed:
            sync_travel_event(event, feed)
    except Exception:
        logger.debug("Failed to sync travel departure to calendar", exc_info=True)


@receiver(post_delete, sender="travel.TravelDeparture")
def remove_travel_departure_from_calendar(sender, instance, **kwargs):
    """Update the parent TravelEvent's CalendarEvent when a departure is deleted."""
    try:
        from .sources.travel import sync_travel_event

        event = instance.event
        if instance.organization_id != event.organization_id:
            return
        feed = _travel_feed_for_event(event)

        if feed:
            sync_travel_event(event, feed)
    except Exception:
        logger.debug("Failed to update travel event on departure delete", exc_info=True)


def _do_sync_outreach_shift(shift):
    """Create or update a CalendarEvent for an OutreachShift."""
    from .models import CalendarFeed
    from .sources.outreach import sync_outreach_shift

    event = shift.event
    if shift.organization_id != event.organization_id or (
        event.program_id and event.program.organization_id != event.organization_id
    ):
        return

    feed = CalendarFeed.objects.filter(
        organization_id=shift.organization_id,
        source_type="outreach",
        program_id=event.program_id,
    ).first()

    if not feed:
        return

    sync_outreach_shift(shift, feed)


def _travel_feed_for_event(event):
    """Return this event's organization-owned feed, preferring its program."""
    from .models import CalendarFeed

    if not event.organization_id:
        return None
    if event.program_id and event.program.organization_id != event.organization_id:
        return None

    feeds = CalendarFeed.objects.filter(
        organization_id=event.organization_id,
        source_type="travel",
    )
    if event.program_id:
        program_feed = feeds.filter(program_id=event.program_id).first()
        if program_feed:
            return program_feed
    return feeds.filter(program__isnull=True).first()
