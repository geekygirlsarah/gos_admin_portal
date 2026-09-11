from datetime import date

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import ListView

from programs.permission_views import LeadMentorRequiredMixin

from .forms import (
    DEFAULT_ACL_INITIAL,
    CalendarEventForm,
    CalendarFeedACLInlineFormSet,
    CalendarFeedForm,
    EventExceptionForm,
)
from .ics import FeedICSView, PublicICSFeedView  # noqa: F401
from .models import CalendarEvent, CalendarFeed, EventException


class CalendarView(View):
    """Main calendar view — shows all feeds the user can read.

    Works for anonymous users too (they only see public feeds); the calendar
    page is exempt from login via the middleware so the outside world can view
    public events.
    """

    def get(self, request):
        from programs.permission_views import (
            can_user_read,
            user_is_mentor_or_lead,
        )

        if request.user.is_authenticated and not can_user_read(
            request.user, "calendar"
        ):
            messages.error(
                request, "You do not have permission to access the calendar."
            )
            return redirect("home")

        feeds = CalendarFeed.objects.filter(is_active=True)
        visible_feeds = [f for f in feeds if f.user_can_read(request.user)]

        # Get the selected feed IDs from query params
        selected = request.GET.getlist("feed")
        if not selected:
            selected = [str(f.pk) for f in visible_feeds]

        context = {
            "feeds": visible_feeds,
            "selected_feed_pks": [int(pk) for pk in selected],
            "can_create_feed": user_is_mentor_or_lead(request.user),
        }
        return render(request, "calendar_feeds/calendar_view.html", context)


class CalendarEventsAPIView(View):
    """JSON API endpoint for FullCalendar to fetch events.

    Anonymous users only receive events from public feeds.
    """

    def get(self, request):
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

        feeds = CalendarFeed.objects.filter(is_active=True)
        if feed_pks:
            feeds = feeds.filter(pk__in=feed_pks)

        all_events = []
        for feed in feeds:
            if not feed.user_can_read(request.user):
                continue
            instances = feed.get_events_for_date_range(start_date, end_date)
            for inst in instances:
                event_data = {
                    "id": f"{inst['pk']}-{inst['date'].isoformat()}",
                    "title": inst["title"],
                    "start": inst["start_datetime"].isoformat(),
                    "end": inst["end_datetime"].isoformat(),
                    "allDay": inst["all_day"],
                    "color": inst["color"],
                    "extendedProps": {
                        "event_pk": inst["pk"],
                        "description": inst["description"],
                        "location": inst["location"],
                        "feed_name": inst["feed_name"],
                        "feed_slug": inst["feed_slug"],
                    },
                }
                all_events.append(event_data)

        return JsonResponse({"events": all_events})


class FeedListView(LeadMentorRequiredMixin, ListView):
    model = CalendarFeed
    template_name = "calendar_feeds/feed_list.html"
    context_object_name = "feeds"


class FeedCreateView(LeadMentorRequiredMixin, View):
    def get(self, request):
        form = CalendarFeedForm()
        acl_formset = CalendarFeedACLInlineFormSet(
            prefix="acls", initial=DEFAULT_ACL_INITIAL
        )
        return render(
            request,
            "calendar_feeds/feed_form.html",
            {"form": form, "acl_formset": acl_formset, "creating": True},
        )

    def post(self, request):
        form = CalendarFeedForm(request.POST)
        acl_formset = CalendarFeedACLInlineFormSet(request.POST, prefix="acls")

        if form.is_valid() and acl_formset.is_valid():
            with transaction.atomic():
                feed = form.save()
                acl_formset.instance = feed
                acl_formset.save()
            messages.success(request, f"Calendar feed '{feed.name}' created.")
            return redirect("calendar_feeds:feed_list")

        return render(
            request,
            "calendar_feeds/feed_form.html",
            {"form": form, "acl_formset": acl_formset, "creating": True},
        )


class FeedUpdateView(LeadMentorRequiredMixin, View):
    def get(self, request, pk):
        feed = get_object_or_404(CalendarFeed, pk=pk)
        form = CalendarFeedForm(instance=feed)
        acl_formset = CalendarFeedACLInlineFormSet(instance=feed, prefix="acls")
        return render(
            request,
            "calendar_feeds/feed_form.html",
            {"form": form, "acl_formset": acl_formset, "feed": feed, "creating": False},
        )

    def post(self, request, pk):
        feed = get_object_or_404(CalendarFeed, pk=pk)
        form = CalendarFeedForm(request.POST, instance=feed)
        acl_formset = CalendarFeedACLInlineFormSet(
            request.POST, instance=feed, prefix="acls"
        )

        if form.is_valid() and acl_formset.is_valid():
            with transaction.atomic():
                form.save()
                acl_formset.save()
            messages.success(request, f"Calendar feed '{feed.name}' updated.")
            return redirect("calendar_feeds:feed_list")

        return render(
            request,
            "calendar_feeds/feed_form.html",
            {"form": form, "acl_formset": acl_formset, "feed": feed, "creating": False},
        )


class FeedDeleteView(LeadMentorRequiredMixin, View):
    def post(self, request, pk):
        feed = get_object_or_404(CalendarFeed, pk=pk)
        name = feed.name
        feed.delete()
        messages.success(request, f"Calendar feed '{name}' deleted.")
        return redirect("calendar_feeds:feed_list")


class FeedACLUpdateView(LeadMentorRequiredMixin, View):
    def get(self, request, pk):
        feed = get_object_or_404(CalendarFeed, pk=pk)
        acl_formset = CalendarFeedACLInlineFormSet(instance=feed, prefix="acls")
        return render(
            request,
            "calendar_feeds/feed_acls.html",
            {"feed": feed, "acl_formset": acl_formset},
        )

    def post(self, request, pk):
        feed = get_object_or_404(CalendarFeed, pk=pk)
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


class EventCreateView(LoginRequiredMixin, View):
    def get(self, request, feed_pk):
        feed = get_object_or_404(CalendarFeed, pk=feed_pk)
        if not feed.user_can_write(request.user):
            messages.error(
                request, "You do not have permission to create events on this feed."
            )
            return redirect("calendar_feeds:calendar_view")

        form = CalendarEventForm(initial={"feed": feed})
        return render(
            request,
            "calendar_feeds/event_form.html",
            {"form": form, "feed": feed, "creating": True},
        )

    def post(self, request, feed_pk):
        feed = get_object_or_404(CalendarFeed, pk=feed_pk)
        if not feed.user_can_write(request.user):
            messages.error(
                request, "You do not have permission to create events on this feed."
            )
            return redirect("calendar_feeds:calendar_view")

        form = CalendarEventForm(request.POST)
        if form.is_valid():
            event = form.save(commit=False)
            event.feed = feed
            event.created_by = request.user
            event.save()
            messages.success(request, f"Event '{event.title}' created.")
            return redirect("calendar_feeds:calendar_view")

        return render(
            request,
            "calendar_feeds/event_form.html",
            {"form": form, "feed": feed, "creating": True},
        )


class EventUpdateView(LoginRequiredMixin, View):
    def get(self, request, pk):
        event = get_object_or_404(CalendarEvent, pk=pk)
        if not event.feed.user_can_write(request.user):
            messages.error(request, "You do not have permission to edit this event.")
            return redirect("calendar_feeds:calendar_view")

        form = CalendarEventForm(instance=event)
        return render(
            request,
            "calendar_feeds/event_form.html",
            {
                "form": form,
                "feed": event.feed,
                "event": event,
                "creating": False,
            },
        )

    def post(self, request, pk):
        event = get_object_or_404(CalendarEvent, pk=pk)
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
            {
                "form": form,
                "feed": event.feed,
                "event": event,
                "creating": False,
            },
        )


class EventDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        event = get_object_or_404(CalendarEvent, pk=pk)
        if not event.feed.user_can_write(request.user):
            messages.error(request, "You do not have permission to delete this event.")
            return redirect("calendar_feeds:calendar_view")

        title = event.title
        event.delete()
        messages.success(request, f"Event '{title}' deleted.")
        return redirect("calendar_feeds:calendar_view")


class EventExceptionListView(LoginRequiredMixin, View):
    def get(self, request, event_pk):
        event = get_object_or_404(CalendarEvent, pk=event_pk)
        if not event.feed.user_can_read(request.user):
            messages.error(request, "You do not have permission to view this event.")
            return redirect("calendar_feeds:calendar_view")
        exceptions = event.exceptions.select_related("created_by").all()
        return render(
            request,
            "calendar_feeds/exception_list.html",
            {"event": event, "exceptions": exceptions},
        )


class EventExceptionCreateView(LoginRequiredMixin, View):
    def get(self, request, event_pk):
        event = get_object_or_404(CalendarEvent, pk=event_pk)
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
        event = get_object_or_404(CalendarEvent, pk=event_pk)
        if not event.feed.user_can_write(request.user):
            messages.error(request, "You do not have permission to modify this event.")
            return redirect("calendar_feeds:calendar_view")

        form = EventExceptionForm(request.POST)
        if form.is_valid():
            exception = form.save(commit=False)
            exception.event = event
            exception.created_by = request.user
            exception.save()
            messages.success(request, "Event exception created.")
            return redirect("calendar_feeds:calendar_view")

        return render(
            request,
            "calendar_feeds/exception_form.html",
            {"form": form, "event": event},
        )


class EventExceptionUpdateView(LoginRequiredMixin, View):
    def get(self, request, pk):
        exception = get_object_or_404(EventException, pk=pk)
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
        exception = get_object_or_404(EventException, pk=pk)
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


class EventExceptionDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        exception = get_object_or_404(EventException, pk=pk)
        if not exception.event.feed.user_can_write(request.user):
            messages.error(request, "You do not have permission to modify this event.")
            return redirect("calendar_feeds:calendar_view")

        exception.delete()
        messages.success(request, "Event exception removed.")
        return redirect("calendar_feeds:calendar_view")
