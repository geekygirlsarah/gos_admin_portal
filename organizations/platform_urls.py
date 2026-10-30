"""URL routing for Platform Administration Dashboard."""

from django.urls import path

from . import platform_views as views

app_name = "platform_admin"

urlpatterns = [
    path("", views.PlatformDashboardView.as_view(), name="dashboard"),
    # Organizations Management
    path(
        "organizations/",
        views.PlatformOrganizationListView.as_view(),
        name="organization_list",
    ),
    path(
        "organizations/new/",
        views.PlatformOrganizationCreateView.as_view(),
        name="organization_create",
    ),
    path(
        "organizations/<int:pk>/",
        views.PlatformOrganizationDetailView.as_view(),
        name="organization_detail",
    ),
    path(
        "organizations/<int:pk>/edit/",
        views.PlatformOrganizationUpdateView.as_view(),
        name="organization_update",
    ),
    path(
        "organizations/<int:pk>/toggle-status/",
        views.PlatformOrganizationToggleStatusView.as_view(),
        name="organization_toggle_status",
    ),
    path(
        "organizations/<int:pk>/members/add/",
        views.PlatformOrganizationMemberAddView.as_view(),
        name="organization_member_add",
    ),
    path(
        "organizations/<int:pk>/members/<int:member_id>/edit/",
        views.PlatformOrganizationMemberUpdateView.as_view(),
        name="organization_member_update",
    ),
    path(
        "organizations/<int:pk>/members/<int:member_id>/delete/",
        views.PlatformOrganizationMemberDeleteView.as_view(),
        name="organization_member_delete",
    ),
    path(
        "organizations/<int:pk>/switch-context/",
        views.PlatformSwitchContextView.as_view(),
        name="organization_switch_context",
    ),
    # Metrics & Health
    path("metrics/", views.PlatformMetricsView.as_view(), name="metrics"),
    path("health/", views.PlatformHealthCheckView.as_view(), name="health"),
    # Support Tools & Users
    path("users/", views.PlatformUserSupportView.as_view(), name="user_support"),
    path(
        "users/<int:pk>/toggle-status/",
        views.PlatformUserToggleStatusView.as_view(),
        name="user_toggle_status",
    ),
    # Impersonation
    path(
        "impersonate/stop/",
        views.PlatformStopImpersonationView.as_view(),
        name="impersonate_stop",
    ),
    path(
        "impersonate/<int:pk>/",
        views.PlatformImpersonateUserView.as_view(),
        name="impersonate_user",
    ),
    # Audit Logs Explorer
    path("audit/", views.PlatformAuditLogView.as_view(), name="audit_logs"),
]
