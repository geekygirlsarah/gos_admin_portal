from django.contrib import admin

from travel.models import (
    TravelCostOption,
    TravelDeparture,
    TravelEvent,
    TravelMentorSignup,
    TravelParentChaperoneSignup,
    TravelSignup,
)


class TravelDepartureInline(admin.TabularInline):
    model = TravelDeparture
    extra = 0
    fields = ("label", "leave_date", "leave_time", "meeting_location", "notes")


class TravelCostOptionInline(admin.TabularInline):
    model = TravelCostOption
    extra = 2
    fields = ("name", "amount", "unit_label", "sort_order")


class TravelSignupInline(admin.TabularInline):
    model = TravelSignup
    extra = 0
    fields = (
        "student",
        "status",
        "departure",
        "approved_total",
        "approved_by",
        "permission_acknowledged",
    )
    readonly_fields = ("approved_total", "approved_by", "permission_acknowledged")
    autocomplete_fields = ["student"]


class TravelMentorSignupInline(admin.TabularInline):
    model = TravelMentorSignup
    extra = 0
    fields = ("adult", "departure", "is_driver")
    autocomplete_fields = ["adult"]


class TravelParentChaperoneSignupInline(admin.TabularInline):
    model = TravelParentChaperoneSignup
    extra = 0
    fields = ("adult", "departure")
    autocomplete_fields = ["adult"]


@admin.register(TravelEvent)
class TravelEventAdmin(admin.ModelAdmin):
    list_display = ("name", "program", "start_date", "end_date", "is_past")
    list_select_related = ("program",)
    search_fields = ("name", "location_name", "program__name")
    list_filter = ("program", "start_date")
    inlines = [
        TravelDepartureInline,
        TravelCostOptionInline,
        TravelSignupInline,
        TravelMentorSignupInline,
        TravelParentChaperoneSignupInline,
    ]
    fieldsets = (
        (None, {"fields": ("program", "name", "start_date", "end_date")}),
        (
            "Location",
            {"fields": ("location_name", "location_address"), "classes": ("wide",)},
        ),
        ("Plans", {"fields": ("description", "permission_form")}),
    )

    @admin.display(boolean=True, description="Past")
    def is_past(self, obj):
        return obj.is_past


@admin.register(TravelDeparture)
class TravelDepartureAdmin(admin.ModelAdmin):
    list_display = ("event", "label", "leave_date", "leave_time")
    list_select_related = ("event__program",)
    autocomplete_fields = ["event"]


@admin.register(TravelCostOption)
class TravelCostOptionAdmin(admin.ModelAdmin):
    list_display = ("event", "name", "amount", "unit_label", "sort_order")
    list_select_related = ("event__program",)
    autocomplete_fields = ["event"]


@admin.register(TravelSignup)
class TravelSignupAdmin(admin.ModelAdmin):
    list_display = (
        "student",
        "event",
        "status",
        "departure",
        "approved_total",
        "approved_by",
    )
    list_select_related = ("student", "event", "departure", "approved_by")
    list_filter = ("status", "event__program")
    search_fields = ("student__legal_first_name", "student__last_name", "event__name")
    autocomplete_fields = ["student", "event"]


@admin.register(TravelMentorSignup)
class TravelMentorSignupAdmin(admin.ModelAdmin):
    list_display = ("adult", "event", "departure", "is_driver")
    list_select_related = ("adult", "event", "departure")
    list_filter = ("is_driver", "event__program")
    search_fields = ("adult__legal_first_name", "adult__last_name", "event__name")


@admin.register(TravelParentChaperoneSignup)
class TravelParentChaperoneSignupAdmin(admin.ModelAdmin):
    list_display = ("adult", "event", "departure", "created_at")
    list_select_related = ("adult", "event", "departure")
    list_filter = ("event__program",)
    search_fields = ("adult__legal_first_name", "adult__last_name", "event__name")
    autocomplete_fields = ["adult", "event"]
