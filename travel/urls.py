from django.urls import path

from travel import views

app_name = "travel"

urlpatterns = [
    path("", views.TravelEventListView.as_view(), name="event_list"),
    path("create/", views.TravelEventCreateView.as_view(), name="event_create"),
    path("<int:pk>/edit/", views.TravelEventUpdateView.as_view(), name="event_edit"),
    path(
        "<int:pk>/delete/", views.TravelEventDeleteView.as_view(), name="event_delete"
    ),
    path("events/<int:pk>/signup/", views.TravelSignupView.as_view(), name="signup"),
    path(
        "signups/<int:pk>/rejoin/",
        views.TravelSignupRejoinView.as_view(),
        name="signup_rejoin",
    ),
    path(
        "signups/<int:pk>/withdraw/",
        views.TravelSignupWithdrawView.as_view(),
        name="signup_withdraw",
    ),
    path(
        "signups/<int:pk>/approve/",
        views.TravelSignupApproveView.as_view(),
        name="signup_approve",
    ),
    path(
        "signups/<int:pk>/decline/",
        views.TravelSignupDeclineView.as_view(),
        name="signup_decline",
    ),
    path(
        "signups/<int:pk>/undo/",
        views.TravelSignupUndoView.as_view(),
        name="signup_undo",
    ),
    path(
        "events/<int:pk>/mentor-signup/",
        views.TravelMentorSignupView.as_view(),
        name="mentor_signup",
    ),
    path(
        "events/<int:pk>/mentor-cancel/",
        views.TravelMentorCancelView.as_view(),
        name="mentor_cancel",
    ),
    path(
        "mentor-signups/<int:pk>/driver/",
        views.TravelMentorDriverToggleView.as_view(),
        name="mentor_driver_toggle",
    ),
    path(
        "events/<int:pk>/chaperone-signup/",
        views.TravelParentChaperoneSignupView.as_view(),
        name="chaperone_signup",
    ),
    path(
        "events/<int:pk>/chaperone-cancel/",
        views.TravelParentChaperoneCancelView.as_view(),
        name="chaperone_cancel",
    ),
    path(
        "chaperone-signups/<int:pk>/remove/",
        views.TravelParentChaperoneRemoveView.as_view(),
        name="chaperone_remove",
    ),
]
