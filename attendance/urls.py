from django.urls import path

from . import views

urlpatterns = [
    path(
        "hours/<int:pk>/",
        views.student_hours_view,
        name="student_hours",
    ),
    path(
        "hours-chart/",
        views.attendance_hours_chart_view,
        name="attendance_hours_chart",
    ),
    path(
        "program-hours/<int:program_id>/",
        views.program_hours_view,
        name="program_hours",
    ),
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
