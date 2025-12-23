from django.urls import path

from . import views

urlpatterns = [
    path("attendance/tap", views.attendance_tap, name="api_attendance_tap"),
    path(
        "attendance/student/<int:student_id>/weekly",
        views.student_weekly_hours,
        name="api_student_weekly_hours",
    ),
    path(
        "attendance/program/<int:program_id>/weekly",
        views.program_weekly_hours,
        name="api_program_weekly_hours",
    ),
]
