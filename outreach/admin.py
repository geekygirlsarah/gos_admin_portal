from django.contrib import admin

from outreach.models import OutreachEvent, OutreachShift, OutreachSignup


class OutreachShiftInline(admin.TabularInline):
    model = OutreachShift
    extra = 1


class OutreachSignupInline(admin.TabularInline):
    model = OutreachSignup
    extra = 0
    autocomplete_fields = ["student"]


@admin.register(OutreachEvent)
class OutreachEventAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "program",
        "location_name",
        "start_date",
        "end_date",
    )
    list_select_related = ("program",)
    search_fields = ("name", "location_name", "location_address")
    inlines = [OutreachShiftInline]


@admin.register(OutreachShift)
class OutreachShiftAdmin(admin.ModelAdmin):
    list_display = (
        "event",
        "date",
        "start_time",
        "end_time",
        "max_champions",
        "max_helpers",
    )
    list_select_related = ("event",)
    search_fields = ("event__name",)
    inlines = [OutreachSignupInline]
