from django.urls import path

from . import views

urlpatterns = [
    path("rfid/", views.rfid_management_view, name="rfid_management"),
    path("active/", views.who_is_here_view, name="attendance_active"),
    path(
        "active/close/<int:pk>/",
        views.close_attendance_session,
        name="close_attendance_session",
    ),
    path(
        "active/close-stale/",
        views.close_stale_attendance_sessions,
        name="close_stale_attendance_sessions",
    ),
    path("summary/", views.attendance_summary_view, name="attendance_summary"),
    path("all/", views.AllAttendanceView.as_view(), name="all_attendance"),
    path("visitors/", views.VisitorManagementView.as_view(), name="visitor_management"),
]
