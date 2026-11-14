from datetime import date, datetime, time

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import IntegrityError, transaction
from django.db.models import Case, CharField, F, Value, When
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import ListView

from audit.service import log_deletion
from programs.permission_views import LeadMentorRequiredMixin, user_is_lead_mentor

from .forms import (
    CalendarEventForm,
    CalendarFeedACLInlineFormSet,
    CalendarFeedForm,
    EventExceptionForm,
)
from .ics import FeedICSView, PersonalICSFeedView, PublicICSFeedView  # noqa: F401
from .models import CalendarEvent, CalendarFeed, EventException, UserCalendarToken
from .occurrence_forms import OccurrenceForm
from .utils import normalize_rrule_until


def _tenant_calendar_events(organization):
    return CalendarEvent.objects.for_organization(organization).filter(
        feed__organization=organization
    )


def _tenant_event_exceptions(organization):
    return EventException.objects.for_organization(organization).filter(
        event__organization=organization,
        event__feed__organization=organization,
    )


def _event_form_context(request, **context):
    context["can_manage_feeds"] = user_is_lead_mentor(
        request.user, org=getattr(request, "organization", None)
    )
    return context


class CalendarView(View):
    """Main calendar view — shows all feeds the user can read.

    Works for anonymous users too (they only see public feeds); the calendar
    page is exempt from login via the middleware so the outside world can view
    public events.
    """

    def get(self, request):
        org = getattr(request, "organization", None)
        if org is None:
            raise Http404

        from programs.permission_views import (
            can_user_read,
            user_is_mentor_or_lead,
        )

        if request.user.is_authenticated and not can_user_read(
            request.user, "calendar", org=org
        ):
            messages.error(
                request, "You do not have permission to access the calendar."
            )
            return redirect("home")

        feeds = CalendarFeed.objects.for_organization(org).filter(is_active=True)
        visible_feeds = [f for f in feeds if f.user_can_read(request.user)]
        writable_feeds = [f for f in visible_feeds if f.user_can_write(request.user)]

        # Get the selected feed IDs from query params
        selected = request.GET.getlist("feed")
        if not selected:
            selected = [str(f.pk) for f in visible_feeds]

        personal_ics_url = None
        user_token = None
        if request.user.is_authenticated:
            token_obj = UserCalendarToken.for_user(request.user)
            user_token = token_obj.token
            personal_ics_url = request.build_absolute_uri(
                reverse("calendar_feeds:ics_personal", kwargs={"token": user_token})
            )

        public_ics_url = request.build_absolute_uri(
            reverse("calendar_feeds:ics_public")
        )

        context = {
            "feeds": visible_feeds,
            "writable_feeds": writable_feeds,
            "selected_feed_pks": [int(pk) for pk in selected],
            "can_create_feed": user_is_mentor_or_lead(
                request.user, org=request.organization
            ),
            "personal_ics_url": personal_ics_url,
            "public_ics_url": public_ics_url,
            "user_token": user_token,
        }
        return render(request, "calendar_feeds/calendar_view.html", context)


class RegenerateCalendarTokenView(LoginRequiredMixin, View):
    """Regenerate the user's personal calendar subscription token."""

    def post(self, request):
        token_obj = UserCalendarToken.for_user(request.user)
        token_obj.regenerate()
        messages.success(
            request, "Your calendar subscription link has been regenerated."
        )
        return redirect("calendar_feeds:calendar_view")


class CalendarEventsAPIView(View):
    """JSON API endpoint for FullCalendar to fetch events.

    Anonymous users only receive events from public feeds.
    """

    def get(self, request):
        org = getattr(request, "organization", None)
        if org is None:
            raise Http404

        start = request.GET.get("start")
        end = request.GET.get("end")
        feed_pks = request.GET.getlist("feed")

        if not start or not end:
            return JsonResponse({"events": []})

        try:
            start_date = date.fromisoformat(start)
            end_date = date.fromisoformat(end)
        except (ValueError, TypeError):
            return JsonResponse({"events": []})

        feeds = CalendarFeed.objects.for_organization(org).filter(is_active=True)
        if feed_pks:
            feeds = feeds.filter(pk__in=feed_pks)

        all_events = []
        for feed in feeds:
            if not feed.user_can_read(request.user):
                continue
            instances = feed.get_events_for_date_range(start_date, end_date)
            can_write = feed.user_can_write(request.user)
            for inst in instances:
                original_date = inst.get("original_date", inst["date"])
                event_data = {
                    "id": f"{inst['pk']}-{original_date.isoformat()}",
                    "title": inst["title"],
                    "start": inst["start_datetime"].isoformat(),
                    "end": inst["end_datetime"].isoformat(),
                    "allDay": inst["all_day"],
                    "color": inst["color"],
                    "extendedProps": {
                        "event_pk": inst["pk"],
                        "original_date": original_date.isoformat(),
                        "is_recurring": inst["event"].is_recurring,
                        "can_write": can_write,
                        "exception_pk": inst.get("exception_pk"),
                        "description": inst["description"],
                        "location": inst["location"],
                        "feed_name": inst["feed_name"],
                        "feed_slug": inst["feed_slug"],
                    },
                }
                all_events.append(event_data)

        return JsonResponse({"events": all_events})


class OrganizationRequiredMixin:
    def dispatch(self, request, *args, **kwargs):
        if getattr(request, "organization", None) is None:
            raise Http404
        return super().dispatch(request, *args, **kwargs)


def _occurrence(request, pk, original_date):
    event = get_object_or_404(
        _tenant_calendar_events(request.organization).select_related("feed"), pk=pk
    )
    if not event.feed.user_can_write(request.user):
        return event, None, None
    try:
        day = date.fromisoformat(original_date)
        if (
            day.isoformat() != original_date
            or not event.is_recurring
            or day in event.exdate_list
        ):
            raise ValueError
        from dateutil.rrule import rrulestr

        rule = rrulestr(
            normalize_rrule_until(event.rrule_ical), dtstart=event.start_datetime
        )
        start = timezone.make_aware(datetime.combine(day, time.min), timezone.UTC)
        occurrence = rule.after(start, inc=True)
        if occurrence is None or occurrence.date() != day:
            raise ValueError
    except (ValueError, TypeError, OverflowError):
        raise Http404
    exception = EventException.objects.filter(event=event, original_date=day).first()
    if exception and (
        exception.organization_id != event.organization_id
        or exception.change_type == EventException.CHANGE_DELETED
    ):
        raise Http404
    return event, occurrence, exception


class OccurrenceEditView(OrganizationRequiredMixin, LoginRequiredMixin, View):
    def handle(self, request, pk, original_date):
        event, occurrence, exception = _occurrence(request, pk, original_date)
        if occurrence is None:
            messages.error(request, "You do not have permission to modify this event.")
            return redirect("calendar_feeds:calendar_view")
        start = (
            exception.new_start_datetime
            if exception and exception.new_start_datetime
            else occurrence
        )
        end = (
            exception.new_end_datetime
            if exception and exception.new_end_datetime
            else occurrence + (event.end_datetime - event.start_datetime)
        )
        initial = {
            "title": exception.new_title or event.title if exception else event.title,
            "location": (
                exception.new_location or event.location
                if exception
                else event.location
            ),
            "description": (
                exception.new_description or event.description
                if exception
                else event.description
            ),
            "start_datetime": start,
            "end_datetime": end,
        }
        form = OccurrenceForm(
            request.POST if request.method == "POST" else None,
            event=event,
            initial=initial,
        )
        if request.method == "POST" and form.is_valid():
            try:
                with transaction.atomic():
                    locked = (
                        _tenant_calendar_events(request.organization)
                        .select_for_update()
                        .get(pk=event.pk)
                    )
                    existing = EventException.objects.filter(
                        event=locked, original_date=occurrence.date()
                    ).first()
                    if existing and (
                        existing.organization_id != locked.organization_id
                        or existing.change_type == EventException.CHANGE_DELETED
                    ):
                        raise Http404
                    obj = existing or EventException(
                        event=locked,
                        organization=locked.organization,
                        original_date=occurrence.date(),
                        created_by=request.user,
                    )
                    new_start = form.cleaned_data["start_datetime"]
                    new_end = form.cleaned_data["end_datetime"]
                    original_end = occurrence + (
                        locked.end_datetime - locked.start_datetime
                    )
                    moved = new_start != occurrence or new_end != original_end
                    obj.change_type = (
                        EventException.CHANGE_MOVED
                        if moved
                        else EventException.CHANGE_CHANGED
                    )
                    obj.new_start_datetime = new_start if moved else None
                    obj.new_end_datetime = new_end if moved else None
                    obj.new_title = (
                        form.cleaned_data["title"]
                        if form.cleaned_data["title"] != locked.title
                        else ""
                    )
                    obj.new_location = (
                        form.cleaned_data["location"]
                        if form.cleaned_data["location"] != locked.location
                        else ""
                    )
                    obj.new_description = (
                        form.cleaned_data["description"]
                        if form.cleaned_data["description"] != locked.description
                        else ""
                    )
                    obj.save()
            except IntegrityError:
                form.add_error(
                    None, "This occurrence was modified concurrently. Please try again."
                )
            else:
                return redirect("calendar_feeds:calendar_view")
        return render(
            request,
            "calendar_feeds/occurrence_form.html",
            {"form": form, "event": event, "original_date": original_date},
        )

    get = handle
    post = handle


class OccurrenceDeleteView(OrganizationRequiredMixin, LoginRequiredMixin, View):
    def handle(self, request, pk, original_date):
        event, occurrence, exception = _occurrence(request, pk, original_date)
        if occurrence is None:
            messages.error(request, "You do not have permission to modify this event.")
            return redirect("calendar_feeds:calendar_view")
        if request.method == "POST":
            try:
                with transaction.atomic():
                    locked = (
                        _tenant_calendar_events(request.organization)
                        .select_for_update()
                        .get(pk=event.pk)
                    )
                    existing = EventException.objects.filter(
                        event=locked, original_date=occurrence.date()
                    ).first()
                    if existing and (
                        existing.organization_id != locked.organization_id
                        or existing.change_type == EventException.CHANGE_DELETED
                    ):
                        raise Http404
                    obj = existing or EventException(
                        event=locked,
                        organization=locked.organization,
                        original_date=occurrence.date(),
                        created_by=request.user,
                    )
                    obj.change_type = EventException.CHANGE_DELETED
                    obj.save()
            except IntegrityError:
                messages.error(
                    request,
                    "This occurrence was modified concurrently. Please try again.",
                )
            return redirect("calendar_feeds:calendar_view")
        return render(
            request,
            "calendar_feeds/occurrence_form.html",
            {"event": event, "original_date": original_date, "deleting": True},
        )

    get = handle
    post = handle


class FeedListView(OrganizationRequiredMixin, LeadMentorRequiredMixin, ListView):
    model = CalendarFeed
    template_name = "calendar_feeds/feed_list.html"
    context_object_name = "feeds"

    def get_queryset(self):
        org = getattr(self.request, "organization", None)
        return (
            CalendarFeed.objects.for_organization(org)
            .annotate(
                local_program_name=Case(
                    When(program__organization_id=org.pk, then=F("program__name")),
                    default=Value(""),
                    output_field=CharField(),
                )
            )
            .order_by("name")
        )


class FeedCreateView(OrganizationRequiredMixin, LeadMentorRequiredMixin, View):
    def get(self, request):
        org = getattr(request, "organization", None)
        form = CalendarFeedForm(organization=org)
        acl_formset = CalendarFeedACLInlineFormSet(prefix="acls")
        return render(
            request,
            "calendar_feeds/feed_form.html",
            {"form": form, "acl_formset": acl_formset, "creating": True},
        )

    def post(self, request):
        org = getattr(request, "organization", None)
        form = CalendarFeedForm(request.POST, organization=org)
        acl_formset = CalendarFeedACLInlineFormSet(request.POST, prefix="acls")

        if form.is_valid() and acl_formset.is_valid():
            with transaction.atomic():
                feed = form.save(commit=False)
                feed.organization = org
                feed.save()
                acl_formset.instance = feed
                acl_formset.save()
            if feed.source_type == CalendarFeed.SOURCE_OUTREACH:
                from .sources.outreach import initial_sync

                initial_sync(feed)
            elif feed.source_type == CalendarFeed.SOURCE_TRAVEL:
                from .sources.travel import initial_sync

                initial_sync(feed)
            messages.success(request, f"Calendar feed '{feed.name}' created.")
            return redirect("calendar_feeds:feed_list")

        return render(
            request,
            "calendar_feeds/feed_form.html",
            {"form": form, "acl_formset": acl_formset, "creating": True},
        )


class FeedUpdateView(OrganizationRequiredMixin, LeadMentorRequiredMixin, View):
    def get(self, request, pk):
        org = getattr(request, "organization", None)
        feed = get_object_or_404(CalendarFeed.objects.for_organization(org), pk=pk)
        form = CalendarFeedForm(instance=feed, organization=org)
        acl_formset = CalendarFeedACLInlineFormSet(instance=feed, prefix="acls")
        return render(
            request,
            "calendar_feeds/feed_form.html",
            {"form": form, "acl_formset": acl_formset, "feed": feed, "creating": False},
        )

    def post(self, request, pk):
        org = getattr(request, "organization", None)
        feed = get_object_or_404(CalendarFeed.objects.for_organization(org), pk=pk)
        form = CalendarFeedForm(request.POST, instance=feed, organization=org)
        acl_formset = CalendarFeedACLInlineFormSet(
            request.POST, instance=feed, prefix="acls"
        )

        if form.is_valid() and acl_formset.is_valid():
            with transaction.atomic():
                form.save()
                acl_formset.save()
            if feed.source_type == CalendarFeed.SOURCE_OUTREACH:
                from .sources.outreach import initial_sync

                initial_sync(feed)
            elif feed.source_type == CalendarFeed.SOURCE_TRAVEL:
                from .sources.travel import initial_sync

                initial_sync(feed)
            messages.success(request, f"Calendar feed '{feed.name}' updated.")
            return redirect("calendar_feeds:feed_list")

        return render(
            request,
            "calendar_feeds/feed_form.html",
            {"form": form, "acl_formset": acl_formset, "feed": feed, "creating": False},
        )


class FeedDeleteView(OrganizationRequiredMixin, LeadMentorRequiredMixin, View):
    def post(self, request, pk):
        org = getattr(request, "organization", None)
        with transaction.atomic():
            feed = get_object_or_404(
                CalendarFeed.objects.for_organization(org).select_for_update(), pk=pk
            )
            # Lock the cascade tree before checking ownership and deleting it.
            list(feed.acls.select_for_update().values_list("pk", flat=True))
            list(feed.events.select_for_update().values_list("pk", flat=True))
            list(
                EventException.objects.filter(event__feed=feed)
                .select_for_update()
                .values_list("pk", flat=True)
            )
            if feed.has_foreign_owned_children():
                messages.error(
                    request,
                    "This feed cannot be deleted while it has records owned by another organization.",
                )
                return redirect("calendar_feeds:feed_list")
            name = feed.name
            log_deletion(
                resource=feed,
                request=request,
                before={
                    "name": name,
                    "slug": feed.slug,
                    "source_type": feed.source_type,
                },
                notes=f"Calendar feed '{name}' deleted.",
            )
            feed.delete()
        messages.success(request, f"Calendar feed '{name}' deleted.")
        return redirect("calendar_feeds:feed_list")


class FeedACLUpdateView(OrganizationRequiredMixin, LeadMentorRequiredMixin, View):
    def get(self, request, pk):
        org = getattr(request, "organization", None)
        feed = get_object_or_404(CalendarFeed.objects.for_organization(org), pk=pk)
        acl_formset = CalendarFeedACLInlineFormSet(instance=feed, prefix="acls")
        return render(
            request,
            "calendar_feeds/feed_acls.html",
            {"feed": feed, "acl_formset": acl_formset},
        )

    def post(self, request, pk):
        org = getattr(request, "organization", None)
        feed = get_object_or_404(CalendarFeed.objects.for_organization(org), pk=pk)
        acl_formset = CalendarFeedACLInlineFormSet(
            request.POST, instance=feed, prefix="acls"
        )
        if acl_formset.is_valid():
            acl_formset.save()
            messages.success(request, f"Permissions for '{feed.name}' updated.")
            return redirect("calendar_feeds:feed_list")
        return render(
            request,
            "calendar_feeds/feed_acls.html",
            {"feed": feed, "acl_formset": acl_formset},
        )


class EventCreateView(OrganizationRequiredMixin, LoginRequiredMixin, View):
    def get(self, request, feed_pk):
        org = getattr(request, "organization", None)
        feed = get_object_or_404(CalendarFeed.objects.for_organization(org), pk=feed_pk)
        if not feed.user_can_write(request.user):
            messages.error(
                request, "You do not have permission to create events on this feed."
            )
            return redirect("calendar_feeds:calendar_view")

        form = CalendarEventForm(initial={"feed": feed})
        return render(
            request,
            "calendar_feeds/event_form.html",
            _event_form_context(request, form=form, feed=feed, creating=True),
        )

    def post(self, request, feed_pk):
        org = getattr(request, "organization", None)
        feed = get_object_or_404(CalendarFeed.objects.for_organization(org), pk=feed_pk)
        if not feed.user_can_write(request.user):
            messages.error(
                request, "You do not have permission to create events on this feed."
            )
            return redirect("calendar_feeds:calendar_view")

        form = CalendarEventForm(request.POST)
        if form.is_valid():
            event = form.save(commit=False)
            event.feed = feed
            event.organization = org
            event.created_by = request.user
            event.save()
            messages.success(request, f"Event '{event.title}' created.")
            return redirect("calendar_feeds:calendar_view")

        return render(
            request,
            "calendar_feeds/event_form.html",
            _event_form_context(request, form=form, feed=feed, creating=True),
        )


class EventUpdateView(OrganizationRequiredMixin, LoginRequiredMixin, View):
    def get(self, request, pk):
        org = getattr(request, "organization", None)
        event = get_object_or_404(_tenant_calendar_events(org), pk=pk)
        if not event.feed.user_can_write(request.user):
            messages.error(request, "You do not have permission to edit this event.")
            return redirect("calendar_feeds:calendar_view")

        form = CalendarEventForm(instance=event)
        return render(
            request,
            "calendar_feeds/event_form.html",
            _event_form_context(
                request,
                form=form,
                feed=event.feed,
                event=event,
                creating=False,
            ),
        )

    def post(self, request, pk):
        org = getattr(request, "organization", None)
        event = get_object_or_404(_tenant_calendar_events(org), pk=pk)
        if not event.feed.user_can_write(request.user):
            messages.error(request, "You do not have permission to edit this event.")
            return redirect("calendar_feeds:calendar_view")

        form = CalendarEventForm(request.POST, instance=event)
        if form.is_valid():
            form.save()
            messages.success(request, f"Event '{event.title}' updated.")
            return redirect("calendar_feeds:calendar_view")

        return render(
            request,
            "calendar_feeds/event_form.html",
            _event_form_context(
                request,
                form=form,
                feed=event.feed,
                event=event,
                creating=False,
            ),
        )


class EventDeleteView(OrganizationRequiredMixin, LoginRequiredMixin, View):
    def post(self, request, pk):
        org = getattr(request, "organization", None)
        with transaction.atomic():
            event = get_object_or_404(
                _tenant_calendar_events(org).select_for_update().select_related("feed"),
                pk=pk,
            )
            if not event.feed.user_can_write(request.user):
                messages.error(
                    request, "You do not have permission to delete this event."
                )
                return redirect("calendar_feeds:calendar_view")

            list(event.exceptions.select_for_update().values_list("pk", flat=True))
            if event.exceptions.exclude(organization_id=org.pk).exists():
                messages.error(
                    request,
                    "This event cannot be deleted while it has records owned by another organization.",
                )
                return redirect("calendar_feeds:calendar_view")

            title = event.title
            log_deletion(
                resource=event,
                request=request,
                before={
                    "title": title,
                    "start_datetime": str(event.start_datetime),
                    "feed": str(event.feed),
                },
                notes=f"Calendar event '{title}' deleted.",
            )
            event.delete()
        messages.success(request, f"Event '{title}' deleted.")
        return redirect("calendar_feeds:calendar_view")


class EventExceptionListView(OrganizationRequiredMixin, LoginRequiredMixin, View):
    def get(self, request, event_pk):
        org = getattr(request, "organization", None)
        event = get_object_or_404(_tenant_calendar_events(org), pk=event_pk)
        if not event.feed.user_can_read(request.user):
            messages.error(request, "You do not have permission to view this event.")
            return redirect("calendar_feeds:calendar_view")
        exceptions = (
            _tenant_event_exceptions(org)
            .filter(event=event)
            .select_related("created_by")
        )
        return render(
            request,
            "calendar_feeds/exception_list.html",
            {"event": event, "exceptions": exceptions},
        )


class EventExceptionCreateView(OrganizationRequiredMixin, LoginRequiredMixin, View):
    def get(self, request, event_pk):
        org = getattr(request, "organization", None)
        event = get_object_or_404(_tenant_calendar_events(org), pk=event_pk)
        if not event.feed.user_can_write(request.user):
            messages.error(request, "You do not have permission to modify this event.")
            return redirect("calendar_feeds:calendar_view")

        form = EventExceptionForm()
        return render(
            request,
            "calendar_feeds/exception_form.html",
            {"form": form, "event": event},
        )

    def post(self, request, event_pk):
        org = getattr(request, "organization", None)
        event = get_object_or_404(_tenant_calendar_events(org), pk=event_pk)
        if not event.feed.user_can_write(request.user):
            messages.error(request, "You do not have permission to modify this event.")
            return redirect("calendar_feeds:calendar_view")

        form = EventExceptionForm(request.POST)
        if form.is_valid():
            exception = form.save(commit=False)
            exception.event = event
            exception.organization = org
            exception.created_by = request.user
            exception.save()
            messages.success(request, "Event exception created.")
            return redirect("calendar_feeds:calendar_view")

        return render(
            request,
            "calendar_feeds/exception_form.html",
            {"form": form, "event": event},
        )


class EventExceptionUpdateView(OrganizationRequiredMixin, LoginRequiredMixin, View):
    def get(self, request, pk):
        org = getattr(request, "organization", None)
        exception = get_object_or_404(_tenant_event_exceptions(org), pk=pk)
        if not exception.event.feed.user_can_write(request.user):
            messages.error(request, "You do not have permission to modify this event.")
            return redirect("calendar_feeds:calendar_view")

        form = EventExceptionForm(instance=exception)
        return render(
            request,
            "calendar_feeds/exception_form.html",
            {
                "form": form,
                "event": exception.event,
                "exception": exception,
            },
        )

    def post(self, request, pk):
        org = getattr(request, "organization", None)
        exception = get_object_or_404(_tenant_event_exceptions(org), pk=pk)
        if not exception.event.feed.user_can_write(request.user):
            messages.error(request, "You do not have permission to modify this event.")
            return redirect("calendar_feeds:calendar_view")

        form = EventExceptionForm(request.POST, instance=exception)
        if form.is_valid():
            form.save()
            messages.success(request, "Event exception updated.")
            return redirect("calendar_feeds:calendar_view")

        return render(
            request,
            "calendar_feeds/exception_form.html",
            {"form": form, "event": exception.event, "exception": exception},
        )


class EventExceptionDeleteView(OrganizationRequiredMixin, LoginRequiredMixin, View):
    def post(self, request, pk):
        org = getattr(request, "organization", None)
        exception = get_object_or_404(_tenant_event_exceptions(org), pk=pk)
        if not exception.event.feed.user_can_write(request.user):
            messages.error(request, "You do not have permission to modify this event.")
            return redirect("calendar_feeds:calendar_view")

        log_deletion(
            resource=exception,
            request=request,
            before={
                "event": str(exception.event),
                "change": exception.get_change_type_display(),
                "original_date": str(exception.original_date),
            },
            notes="Event exception removed.",
        )
        exception.delete()
        messages.success(request, "Event exception removed.")
        return redirect("calendar_feeds:calendar_view")
