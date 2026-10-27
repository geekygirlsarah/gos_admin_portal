import logging
from datetime import date, timedelta

from django.conf import settings
from django.http import HttpResponse
from django.utils import timezone
from django.views import View

from .models import CalendarFeed, UserCalendarToken

logger = logging.getLogger(__name__)


def _escape(text):
    """Escape special characters in iCalendar text fields."""
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def _to_localtime(dt):
    if dt is None:
        return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return timezone.localtime(dt, timezone.get_current_timezone())


def _dt_lines(start, end):
    tz_name = getattr(settings, "TIME_ZONE", "America/New_York")
    local_start = _to_localtime(start)
    local_end = _to_localtime(end)
    return [
        f"DTSTART;TZID={tz_name}:{local_start.strftime('%Y%m%dT%H%M%S')}",
        f"DTEND;TZID={tz_name}:{local_end.strftime('%Y%m%dT%H%M%S')}",
    ]


def _exdate_list(event):
    """EXDATEs from the event's exdate_ical plus deleted exceptions."""
    exdates = list(event.exdate_list)
    for exc in event.exceptions.filter(change_type="deleted"):
        if exc.original_date not in exdates:
            exdates.append(exc.original_date)
    return sorted(exdates)


def _exception_lines(event, exc):
    tz_name = getattr(settings, "TIME_ZONE", "America/New_York")
    lines = ["BEGIN:VEVENT", f"UID:{event.uid}"]
    if event.all_day:
        lines.append(f"RECURRENCE-ID;VALUE=DATE:{exc.original_date.strftime('%Y%m%d')}")
    else:
        local_start = _to_localtime(event.start_datetime)
        time_str = local_start.strftime("%H%M%S")
        lines.append(
            f"RECURRENCE-ID;TZID={tz_name}:{exc.original_date.strftime('%Y%m%d')}T{time_str}"
        )
    lines.extend(_dt_lines(exc.new_start_datetime, exc.new_end_datetime))
    lines.append(f"SUMMARY:{_escape(exc.new_title or event.title)}")
    if exc.new_location or event.location:
        lines.append(f"LOCATION:{_escape(exc.new_location or event.location)}")
    lines.append("END:VEVENT")
    return lines


def _vevent_lines(event):
    lines = ["BEGIN:VEVENT", f"UID:{event.uid}"]

    if event.all_day:
        local_start = _to_localtime(event.start_datetime)
        local_end = _to_localtime(event.end_datetime)
        dtstart = local_start.date().strftime("%Y%m%d")
        dtend = (local_end.date() + timedelta(days=1)).strftime("%Y%m%d")
        lines.append(f"DTSTART;VALUE=DATE:{dtstart}")
        lines.append(f"DTEND;VALUE=DATE:{dtend}")
    else:
        lines.extend(_dt_lines(event.start_datetime, event.end_datetime))

    lines.append(f"SUMMARY:{_escape(event.title)}")
    if event.description:
        lines.append(f"DESCRIPTION:{_escape(event.description)}")
    if event.location:
        lines.append(f"LOCATION:{_escape(event.location)}")
    if event.rrule_ical:
        lines.append(f"RRULE:{event.rrule_ical}")

    exdates = _exdate_list(event)
    if exdates:
        lines.append(f"EXDATE:{','.join(d.strftime('%Y%m%d') for d in exdates)}")

    for exc in event.exceptions.all():
        if exc.change_type == exc.CHANGE_MOVED and exc.new_start_datetime:
            lines.extend(_exception_lines(event, exc))

    lines.append("END:VEVENT")
    return lines


def _build_ics_response(events, feed_name="GoS Calendar"):
    """Build an ICS HTTP response from a list of CalendarEvent objects."""
    tz_name = getattr(settings, "TIME_ZONE", "America/New_York")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//GoS Admin Portal//calendar_feeds//EN",
        f"X-WR-CALNAME:{feed_name}",
        f"X-WR-TIMEZONE:{tz_name}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]

    for event in events:
        lines.extend(_vevent_lines(event))

    lines.append("END:VCALENDAR")

    content = "\r\n".join(lines)
    response = HttpResponse(content, content_type="text/calendar; charset=utf-8")
    response["Content-Disposition"] = f'inline; filename="{feed_name}.ics"'
    return response


class PublicICSFeedView(View):
    """Public ICS feed for all active public-visibility feeds."""

    def get(self, request):
        feeds = CalendarFeed.objects.filter(
            is_active=True, visibility=CalendarFeed.VISIBILITY_ANONYMOUS
        )
        today = date.today()
        # Show events from 30 days ago to 1 year in the future
        start = today - timedelta(days=30)
        end = today + timedelta(days=365)

        all_events = []
        for feed in feeds:
            instances = feed.get_events_for_date_range(start, end)
            for inst in instances:
                all_events.append(inst["event"])

        # Deduplicate by PK (recurring events may appear multiple times)
        seen_pks = set()
        unique_events = []
        for event in all_events:
            if event.pk not in seen_pks:
                seen_pks.add(event.pk)
                unique_events.append(event)

        return _build_ics_response(
            unique_events, feed_name="Girls of Steel Public Calendar"
        )


class FeedICSView(View):
    """ICS feed for a specific calendar feed.

    Supports session-authenticated users and tokenized requests (via URL parameter
    or `?token=` query parameter).
    """

    def get(self, request, slug, token=None):
        feed = CalendarFeed.objects.filter(slug=slug, is_active=True).first()
        if not feed:
            return HttpResponse("Feed not found", status=404)

        # Check access
        if not feed.is_public:
            target_user = None
            if request.user.is_authenticated:
                target_user = request.user
            else:
                token_val = token or request.GET.get("token")
                if token_val:
                    try:
                        token_obj = UserCalendarToken.objects.select_related(
                            "user"
                        ).get(token=token_val)
                        target_user = token_obj.user
                    except (UserCalendarToken.DoesNotExist, ValueError):
                        return HttpResponse("Invalid token", status=401)
                else:
                    return HttpResponse("Authentication required", status=401)

            if not target_user or not feed.user_can_read(target_user):
                return HttpResponse("Access denied", status=403)

        today = date.today()
        start = today - timedelta(days=30)
        end = today + timedelta(days=365)

        instances = feed.get_events_for_date_range(start, end)
        seen_pks = set()
        events = []
        for inst in instances:
            ev = inst["event"]
            if ev.pk not in seen_pks:
                seen_pks.add(ev.pk)
                events.append(ev)

        return _build_ics_response(events, feed_name=feed.name)


class PersonalICSFeedView(View):
    """Unified Personal ICS feed for a specific user token.

    Aggregates all events across all feeds the user is permitted to read.
    """

    def get(self, request, token):
        try:
            token_obj = UserCalendarToken.objects.select_related("user").get(
                token=token
            )
        except (UserCalendarToken.DoesNotExist, ValueError):
            return HttpResponse("Feed not found", status=404)

        user = token_obj.user
        feeds = CalendarFeed.objects.filter(is_active=True)
        today = date.today()
        start = today - timedelta(days=30)
        end = today + timedelta(days=365)

        all_events = []
        for feed in feeds:
            if not feed.user_can_read(user):
                continue
            instances = feed.get_events_for_date_range(start, end)
            for inst in instances:
                all_events.append(inst["event"])

        # Deduplicate by PK
        seen_pks = set()
        unique_events = []
        for event in all_events:
            if event.pk not in seen_pks:
                seen_pks.add(event.pk)
                unique_events.append(event)

        return _build_ics_response(unique_events, feed_name="Girls of Steel Calendar")
