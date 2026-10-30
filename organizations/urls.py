from django.urls import path

from . import views

app_name = "organizations"

urlpatterns = [
    path(
        "invitations/<str:token>/",
        views.AcceptInvitationView.as_view(),
        name="accept_invitation",
    ),
]
