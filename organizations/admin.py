from django.contrib import admin

from .models import (
    Organization,
    OrganizationInvitation,
    OrganizationSettings,
    UserOrganizationMembership,
)


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


@admin.register(UserOrganizationMembership)
class UserOrganizationMembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "organization", "role", "is_active", "created_at")
    list_filter = ("organization", "role", "is_active")
    search_fields = ("user__username", "user__email", "organization__name")
    autocomplete_fields = ("user", "organization", "invited_by")
    readonly_fields = ("created_at", "updated_at")


@admin.register(OrganizationInvitation)
class OrganizationInvitationAdmin(admin.ModelAdmin):
    list_display = (
        "email",
        "organization",
        "role",
        "status",
        "expires_at",
        "created_at",
    )
    list_filter = ("organization", "role", "status")
    search_fields = ("email", "organization__name")
    autocomplete_fields = ("organization", "invited_by", "accepted_by")
    readonly_fields = ("token", "created_at", "accepted_at")
