from django.contrib import admin

from outreach.models import (
    OutreachEvent,
    OutreachMentorSignup,
    OutreachShift,
    OutreachSignup,
)


class OutreachShiftInline(admin.TabularInline):
    model = OutreachShift
    extra = 1


class OutreachSignupInline(admin.TabularInline):
    model = OutreachSignup
    extra = 0
    autocomplete_fields = ["student"]


class OutreachMentorSignupInline(admin.TabularInline):
    model = OutreachMentorSignup
    extra = 0
    autocomplete_fields = ["adult"]
    verbose_name = "mentor support signup"
    verbose_name_plural = "mentor support signups"


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
    inlines = [OutreachSignupInline, OutreachMentorSignupInline]


@admin.register(OutreachMentorSignup)
class OutreachMentorSignupAdmin(admin.ModelAdmin):
    list_display = (
        "adult",
        "event_name",
        "shift_date",
        "created_at",
    )
    list_select_related = ("shift__event", "adult")
    search_fields = (
        "adult__first_name",
        "adult__last_name",
        "shift__event__name",
    )

    @admin.display(description="Event")
    def event_name(self, obj):
        return obj.shift.event.name

    @admin.display(description="Date")
    def shift_date(self, obj):
        return obj.shift.date
