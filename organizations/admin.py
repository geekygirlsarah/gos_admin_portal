from django.contrib import admin

from .models import Organization, OrganizationSettings


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "subdomain", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("name", "slug", "subdomain")
    readonly_fields = ("created_at", "updated_at")


@admin.register(OrganizationSettings)
class OrganizationSettingsAdmin(admin.ModelAdmin):
    list_display = ("organization", "updated_at")
    autocomplete_fields = ("organization",)
    readonly_fields = ("updated_at",)
