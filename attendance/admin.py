from django.contrib import admin

from .models import AttendanceEvent, AttendanceSession, KioskDevice, RFIDCard


@admin.register(KioskDevice)
class KioskDeviceAdmin(admin.ModelAdmin):
    list_display = ("name", "program", "location", "is_active", "api_key", "created_at")
    list_filter = ("program", "is_active")
    search_fields = ("name", "location", "api_key")


@admin.register(RFIDCard)
class RFIDCardAdmin(admin.ModelAdmin):
    list_display = ("uid", "student", "adult", "is_active", "assigned_at")
    list_filter = ("is_active",)
    search_fields = (
        "uid",
        "student__preferred_first_name",
        "student__last_name",
        "adult__legal_first_name",
        "adult__last_name",
    )


@admin.register(AttendanceEvent)
class AttendanceEventAdmin(admin.ModelAdmin):
    list_display = (
        "occurred_at",
        "event_type",
        "program",
        "student",
        "adult",
        "visitor_name",
        "rfid_uid",
        "kiosk",
        "source",
    )
    list_filter = ("program", "event_type", "source")
    search_fields = (
        "visitor_name",
        "rfid_uid",
        "student__preferred_first_name",
        "student__last_name",
        "adult__legal_first_name",
        "adult__last_name",
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
        "adult",
        "visitor_name",
        "is_open",
    )
    list_filter = ("program",)
    search_fields = (
        "visitor_name",
        "student__preferred_first_name",
        "student__last_name",
        "adult__legal_first_name",
        "adult__last_name",
    )
    date_hierarchy = "check_in"
