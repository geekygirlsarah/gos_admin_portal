from django.urls import path

from . import views

app_name = "calendar_feeds"

urlpatterns = [
    # Web calendar view
    path("", views.CalendarView.as_view(), name="calendar_view"),
    # Feed management (Lead Mentor)
    path("feeds/", views.FeedListView.as_view(), name="feed_list"),
    path("feeds/create/", views.FeedCreateView.as_view(), name="feed_create"),
    path("feeds/<int:pk>/edit/", views.FeedUpdateView.as_view(), name="feed_edit"),
    path(
        "feeds/<int:pk>/delete/",
        views.FeedDeleteView.as_view(),
        name="feed_delete",
    ),
    # Feed ACL management
    path(
        "feeds/<int:pk>/acls/",
        views.FeedACLUpdateView.as_view(),
        name="feed_acl_update",
    ),
    # Event management
    path(
        "feeds/<int:feed_pk>/events/create/",
        views.EventCreateView.as_view(),
        name="event_create",
    ),
    path(
        "events/<int:pk>/edit/",
        views.EventUpdateView.as_view(),
        name="event_edit",
    ),
    path(
        "events/<int:pk>/delete/",
        views.EventDeleteView.as_view(),
        name="event_delete",
    ),
    # Event exceptions
    path(
        "events/<int:event_pk>/exceptions/",
        views.EventExceptionListView.as_view(),
        name="exception_list",
    ),
    path(
        "events/<int:event_pk>/exceptions/create/",
        views.EventExceptionCreateView.as_view(),
        name="exception_create",
    ),
    path(
        "exceptions/<int:pk>/edit/",
        views.EventExceptionUpdateView.as_view(),
        name="exception_edit",
    ),
    path(
        "exceptions/<int:pk>/delete/",
        views.EventExceptionDeleteView.as_view(),
        name="exception_delete",
    ),
    # ICS feed endpoints
    path(
        "feed.ics",
        views.PublicICSFeedView.as_view(),
        name="ics_public",
    ),
    path(
        "<slug:slug>/feed.ics",
        views.FeedICSView.as_view(),
        name="ics_feed",
    ),
    # JSON API for FullCalendar
    path(
        "api/events/",
        views.CalendarEventsAPIView.as_view(),
        name="api_events",
    ),
]
