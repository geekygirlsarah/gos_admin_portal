from django.urls import path

from . import views

urlpatterns = [
    path("rfid/", views.rfid_management_view, name="rfid_management"),
    path("manifest/", views.active_manifest_view, name="attendance_manifest"),
    path("summary/", views.attendance_summary_view, name="attendance_summary"),
]
