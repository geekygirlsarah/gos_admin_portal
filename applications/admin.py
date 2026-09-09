"""Django admin registrations for the application wizard models."""

from __future__ import annotations

from django.contrib import admin

from .models import Application


@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):
    list_display = (
        "application_id",
        "applicant_type",
        "email",
        "program",
        "status",
        "current_step",
        "email_verified_at",
        "submitted_at",
        "created_at",
    )
    list_filter = ("status", "applicant_type", "program")
    search_fields = ("application_id", "email")
    readonly_fields = (
        "application_id",
        "email_verified_at",
        "otp_hash",
        "otp_expires_at",
        "otp_attempts",
        "created_at",
        "updated_at",
        "submitted_at",
        "reviewed_at",
        "reviewed_by",
    )
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "application_id",
                    "applicant_type",
                    "email",
                    "program",
                    "status",
                    "current_step",
                )
            },
        ),
        (
            "Captured data",
            {"classes": ("collapse",), "fields": ("data",)},
        ),
        (
            "Email verification",
            {
                "classes": ("collapse",),
                "fields": (
                    "email_verified_at",
                    "otp_hash",
                    "otp_expires_at",
                    "otp_attempts",
                ),
            },
        ),
        (
            "Review",
            {
                "fields": (
                    "decline_reason",
                    "reviewed_at",
                    "reviewed_by",
                )
            },
        ),
        (
            "Audit",
            {
                "classes": ("collapse",),
                "fields": ("created_at", "updated_at", "submitted_at"),
            },
        ),
    )
