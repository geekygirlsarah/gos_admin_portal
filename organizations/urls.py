from django.urls import path

from . import platform_views, views

app_name = "organizations"

urlpatterns = [
    path(
        "support-impersonation/handoff/",
        platform_views.SupportImpersonationHandoffView.as_view(),
        name="support_impersonation_handoff",
    ),
    path(
        "support-impersonation/redeem/",
        platform_views.SupportImpersonationRedeemView.as_view(),
        name="support_impersonation_redeem",
    ),
    path(
        "support-impersonation/resume/",
        platform_views.SupportImpersonationResumeView.as_view(),
        name="support_impersonation_resume",
    ),
    path(
        "support-impersonation/confirm/",
        platform_views.SupportImpersonationConfirmView.as_view(),
        name="support_impersonation_confirm",
    ),
    path(
        "invitations/<str:token>/",
        views.AcceptInvitationView.as_view(),
        name="accept_invitation",
    ),
]
