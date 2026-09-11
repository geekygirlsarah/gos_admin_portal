from django.contrib import admin

from .models import CalendarEvent, CalendarFeed, CalendarFeedACL, EventException


class CalendarFeedACLInline(admin.TabularInline):
    model = CalendarFeedACL
    extra = 0


class EventExceptionInline(admin.TabularInline):
    model = EventException
    extra = 0
    readonly_fields = ("created_by", "created_at")


@admin.register(CalendarFeed)
class CalendarFeedAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "slug",
        "source_type",
        "visibility",
        "program",
        "is_active",
    )
    list_filter = ("is_active", "source_type", "visibility")
    search_fields = ("name", "description")
    prepopulated_fields = {"slug": ("name",)}
    inlines = [CalendarFeedACLInline]


@admin.register(CalendarFeedACL)
class CalendarFeedACLAdmin(admin.ModelAdmin):
    list_display = ("feed", "role", "can_read", "can_write")
    list_filter = ("role", "can_read", "can_write")


@admin.register(CalendarEvent)
class CalendarEventAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "feed",
        "start_datetime",
        "end_datetime",
        "all_day",
        "is_recurring",
    )
    list_filter = ("feed", "all_day")
    search_fields = ("title", "description", "location")
    date_hierarchy = "start_datetime"
    inlines = [EventExceptionInline]


@admin.register(EventException)
class EventExceptionAdmin(admin.ModelAdmin):
    list_display = (
        "event",
        "original_date",
        "change_type",
        "created_by",
        "created_at",
    )
    list_filter = ("change_type",)
