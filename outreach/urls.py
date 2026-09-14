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
    # Saved-location reference list (org-wide, mentor/Lead Mentor managed)
    path("locations/", views.OutreachLocationListView.as_view(), name="location_list"),
    path(
        "locations/create/",
        views.OutreachLocationCreateView.as_view(),
        name="location_create",
    ),
    path(
        "locations/<int:pk>/edit/",
        views.OutreachLocationUpdateView.as_view(),
        name="location_edit",
    ),
    path(
        "locations/<int:pk>/delete/",
        views.OutreachLocationDeleteView.as_view(),
        name="location_delete",
    ),
    path(
        "shifts/<int:shift_pk>/signup/",
        views.OutreachShiftSignupView.as_view(),
        name="shift_signup",
    ),
    path(
        "shifts/<int:shift_pk>/cancel/",
        views.OutreachShiftCancelView.as_view(),
        name="shift_cancel",
    ),
    path(
        "shifts/<int:shift_pk>/mentor-signup/",
        views.OutreachShiftMentorSignupView.as_view(),
        name="shift_mentor_signup",
    ),
    path(
        "shifts/<int:shift_pk>/mentor-cancel/",
        views.OutreachShiftMentorCancelView.as_view(),
        name="shift_mentor_cancel",
    ),
    path(
        "shifts/<int:shift_pk>/manage-signups/",
        views.OutreachShiftManageSignupsView.as_view(),
        name="shift_manage_signups",
    ),
    path(
        "shifts/<int:shift_pk>/check-in/",
        views.OutreachShiftCheckInView.as_view(),
        name="shift_check_in",
    ),
    path(
        "student-stats/",
        views.OutreachStudentStatsView.as_view(),
        name="student_stats",
    ),
]
