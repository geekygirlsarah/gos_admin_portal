from django.urls import path, include
from . import views, kiosk_views

kiosk_patterns = [
    path("request_code/", kiosk_views.kiosk_request_code, name="api_kiosk_request_code"),
    path("unlock/", kiosk_views.kiosk_unlock, name="api_kiosk_unlock"),
    path("lock/", kiosk_views.kiosk_lock, name="api_kiosk_lock"),
    path("tap/", kiosk_views.kiosk_tap, name="api_kiosk_tap"),
    path("lookup/", kiosk_views.kiosk_lookup, name="api_kiosk_lookup"),
]

urlpatterns = [
    path("attendance/tap", views.attendance_tap, name="api_attendance_tap"),
    path(
        "attendance/student/lookup",
        views.student_lookup,
        name="api_student_lookup",
    ),
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
    path("kiosk/<int:kiosk_id>/", include(kiosk_patterns)),
]
