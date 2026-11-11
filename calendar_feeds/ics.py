import logging
from datetime import date, datetime, time, timedelta
from datetime import timezone as datetime_timezone

from dateutil.rrule import rrulestr
from django.conf import settings
from django.http import HttpResponse
from django.utils import timezone
from django.views import View

from organizations.models import UserOrganizationMembership

from .models import CalendarFeed, UserCalendarToken
from .utils import normalize_rrule_until

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


def _event_owned_exceptions(event):
    if event.organization_id != event.feed.organization_id:
        return event.exceptions.none()
    return event.exceptions.filter(organization_id=event.organization_id)


def _dt_lines(start, end):
    tz_name = getattr(settings, "TIME_ZONE", "America/New_York")
    local_start = _to_localtime(start)
    local_end = _to_localtime(end)
    return [
        f"DTSTART;TZID={tz_name}:{local_start.strftime('%Y%m%dT%H%M%S')}",
        f"DTEND;TZID={tz_name}:{local_end.strftime('%Y%m%dT%H%M%S')}",
    ]


def _exdate_list(event, exceptions):
    """EXDATEs from the event's exdate_ical plus deleted exceptions."""
    exdates = set(event.exdate_list)
    for exc in exceptions:
        if exc.change_type == exc.CHANGE_DELETED:
            exdates.add(exc.original_date)
    return sorted(exdates)


def _utc_datetime(dt):
    return _to_localtime(dt).astimezone(datetime_timezone.utc)


def _occurrence_start(event, day):
    start = _utc_datetime(event.start_datetime)
    return start.replace(year=day.year, month=day.month, day=day.day)


def _current_exceptions(event, exceptions):
    if not event.rrule_ical or not exceptions:
        return []
    try:
        rule = rrulestr(
            normalize_rrule_until(event.rrule_ical),
            dtstart=_utc_datetime(event.start_datetime),
        )
        current = []
        for exc in exceptions:
            start = datetime.combine(
                exc.original_date, time.min, tzinfo=datetime_timezone.utc
            )
            occurrence = rule.after(start, inc=True)
            if occurrence is not None and occurrence.date() == exc.original_date:
                current.append(exc)
        return current
    except (ValueError, TypeError, OverflowError):
        logger.warning(
            "Cannot match calendar exceptions to recurrence rule for event %s", event.pk
        )
        return []


def _exception_lines(event, exc):
    lines = ["BEGIN:VEVENT", f"UID:{event.uid}"]
    local_start = _to_localtime(event.start_datetime)
    local_end = _to_localtime(event.end_datetime)
    if event.all_day:
        lines.append(f"RECURRENCE-ID;VALUE=DATE:{exc.original_date.strftime('%Y%m%d')}")
    else:
        occurrence = _occurrence_start(event, exc.original_date)
        lines.append(f"RECURRENCE-ID:{occurrence:%Y%m%dT%H%M%SZ}")
    if exc.change_type == exc.CHANGE_MOVED:
        start, end = exc.new_start_datetime, exc.new_end_datetime
    elif event.all_day:
        delta = exc.original_date - local_start.date()
        start, end = local_start + delta, local_end + delta
    else:
        start = occurrence
        end = start + (
            _utc_datetime(event.end_datetime) - _utc_datetime(event.start_datetime)
        )
    if event.all_day:
        start_date = _to_localtime(start).date()
        end_date = _to_localtime(end).date() + timedelta(days=1)
        lines.append(f"DTSTART;VALUE=DATE:{start_date:%Y%m%d}")
        lines.append(f"DTEND;VALUE=DATE:{end_date:%Y%m%d}")
    else:
        lines.extend(_dt_lines(start, end))
    lines.append(f"SUMMARY:{_escape(exc.new_title or event.title)}")
    if exc.new_description or event.description:
        lines.append(f"DESCRIPTION:{_escape(exc.new_description or event.description)}")
    if exc.new_location or event.location:
        lines.append(f"LOCATION:{_escape(exc.new_location or event.location)}")
    lines.append("END:VEVENT")
    return lines


def _vevent_lines(event, exdates):
    lines = ["BEGIN:VEVENT", f"UID:{event.uid}"]

    if event.all_day:
        local_start = _to_localtime(event.start_datetime)
        local_end = _to_localtime(event.end_datetime)
        dtstart = local_start.date().strftime("%Y%m%d")
        dtend = (local_end.date() + timedelta(days=1)).strftime("%Y%m%d")
        lines.append(f"DTSTART;VALUE=DATE:{dtstart}")
        lines.append(f"DTEND;VALUE=DATE:{dtend}")
    elif event.rrule_ical:
        start = _utc_datetime(event.start_datetime)
        end = _utc_datetime(event.end_datetime)
        lines.extend([f"DTSTART:{start:%Y%m%dT%H%M%SZ}", f"DTEND:{end:%Y%m%dT%H%M%SZ}"])
    else:
        lines.extend(_dt_lines(event.start_datetime, event.end_datetime))

    lines.append(f"SUMMARY:{_escape(event.title)}")
    if event.description:
        lines.append(f"DESCRIPTION:{_escape(event.description)}")
    if event.location:
        lines.append(f"LOCATION:{_escape(event.location)}")
    if event.rrule_ical:
        lines.append(f"RRULE:{event.rrule_ical}")

    if exdates:
        if event.all_day:
            lines.append(
                f"EXDATE;VALUE=DATE:{','.join(d.strftime('%Y%m%d') for d in exdates)}"
            )
        else:
            values = ",".join(
                f"{_occurrence_start(event, day):%Y%m%dT%H%M%SZ}" for day in exdates
            )
            lines.append(f"EXDATE:{values}")

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
        exceptions = _current_exceptions(
            event, list(_event_owned_exceptions(event).order_by("original_date"))
        )
        exdates = _exdate_list(event, exceptions)
        lines.extend(_vevent_lines(event, exdates))
        for exc in exceptions:
            if exc.original_date in exdates:
                continue
            if exc.change_type == exc.CHANGE_CHANGED or (
                exc.change_type == exc.CHANGE_MOVED
                and exc.new_start_datetime
                and exc.new_end_datetime
            ):
                lines.extend(_exception_lines(event, exc))

    lines.append("END:VCALENDAR")

    content = "\r\n".join(lines)
    response = HttpResponse(content, content_type="text/calendar; charset=utf-8")
    response["Content-Disposition"] = f'inline; filename="{feed_name}.ics"'
    return response


class PublicICSFeedView(View):
    """Public ICS feed for all active public-visibility feeds."""

    def get(self, request):
        organization = getattr(request, "organization", None)
        if organization is None:
            return HttpResponse("Organization not found", status=404)

        feeds = CalendarFeed.objects.for_organization(organization).filter(
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
        organization = getattr(request, "organization", None)
        if organization is None:
            return HttpResponse("Organization not found", status=404)

        feed = (
            CalendarFeed.objects.for_organization(organization)
            .filter(slug=slug, is_active=True)
            .first()
        )
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
        organization = getattr(request, "organization", None)
        if organization is None:
            return HttpResponse("Organization not found", status=404)

        try:
            token_obj = UserCalendarToken.objects.select_related("user").get(
                token=token
            )
        except (UserCalendarToken.DoesNotExist, ValueError):
            return HttpResponse("Feed not found", status=404)

        user = token_obj.user
        if not user.is_active:
            return HttpResponse("Access denied", status=403)
        if (
            not user.is_superuser
            and not UserOrganizationMembership.objects.filter(
                user=user, organization=organization, is_active=True
            ).exists()
        ):
            return HttpResponse("Access denied", status=403)

        feeds = CalendarFeed.objects.for_organization(organization).filter(
            is_active=True
        )
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
