from django.contrib import admin

from .models import AttendanceEvent, AttendanceSession, KioskDevice, RFIDCard


@admin.register(KioskDevice)
class KioskDeviceAdmin(admin.ModelAdmin):
    list_display = ("name", "program", "location", "is_active", "api_key", "created_at")
    list_filter = ("program", "is_active")
    search_fields = ("name", "location", "api_key")


@admin.register(RFIDCard)
class RFIDCardAdmin(admin.ModelAdmin):
    list_display = ("uid", "student", "is_active", "assigned_at")
    list_filter = ("is_active",)
    search_fields = ("uid", "student__first_name", "student__last_name")


@admin.register(AttendanceEvent)
class AttendanceEventAdmin(admin.ModelAdmin):
    list_display = (
        "occurred_at",
        "event_type",
        "program",
        "student",
        "visitor_name",
        "rfid_uid",
        "kiosk",
        "source",
    )
    list_filter = ("program", "event_type", "source")
    search_fields = (
        "visitor_name",
        "rfid_uid",
        "student__first_name",
        "student__last_name",
    )
    date_hierarchy = "occurred_at"


@admin.register(AttendanceSession)
class AttendanceSessionAdmin(admin.ModelAdmin):
    list_display = (
        "check_in",
        "check_out",
        "duration_minutes",
        "program",
        "student",
        "visitor_name",
        "is_open",
    )
    list_filter = ("program",)
    search_fields = ("visitor_name", "student__first_name", "student__last_name")
    date_hierarchy = "check_in"
