from django.urls import path

from outreach import views

app_name = "outreach"

urlpatterns = [
    path("", views.OutreachEventListView.as_view(), name="event_list"),
    path("create/", views.OutreachEventCreateView.as_view(), name="event_create"),
    path("<int:pk>/edit/", views.OutreachEventUpdateView.as_view(), name="event_edit"),
    path(
        "<int:pk>/delete/", views.OutreachEventDeleteView.as_view(), name="event_delete"
    ),
    path(
        "<int:pk>/signup/", views.OutreachEventSignupView.as_view(), name="event_signup"
    ),
    path(
        "<int:pk>/cancel/", views.OutreachEventCancelView.as_view(), name="event_cancel"
    ),
    path(
        "<int:pk>/manage-signups/",
        views.OutreachEventManageSignupsView.as_view(),
        name="event_manage_signups",
    ),
    path(
        "student-stats/",
        views.OutreachStudentStatsView.as_view(),
        name="student_stats",
    ),
]
