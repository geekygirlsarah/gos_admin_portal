import csv
import json

from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.utils.html import format_html

from .models import AuditLog

# ---------------------------------------------------------------------------
# Permission helper
# ---------------------------------------------------------------------------


def _is_lead_mentor(user) -> bool:
    """Lead mentors are superusers or members of the 'LeadMentor' group."""
    return user.is_active and (
        user.is_superuser or user.groups.filter(name="LeadMentor").exists()
    )


# ---------------------------------------------------------------------------
# Mixin — applied to every admin class in this module
# ---------------------------------------------------------------------------


class _LeadMentorOnly:
    """Restricts all admin operations to lead mentors; disables mutations."""

    def has_module_perms(self, request):
        return _is_lead_mentor(request.user)

    def has_view_permission(self, request, obj=None):
        return _is_lead_mentor(request.user)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


# ---------------------------------------------------------------------------
# CSV helper (shared by action + export view)
# ---------------------------------------------------------------------------


def _build_csv_response(queryset) -> HttpResponse:
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="audit_log.csv"'
    writer = csv.writer(response)
    writer.writerow(
        [
            "timestamp",
            "event",
            "actor_id",
            "actor_username",
            "resource_type",
            "resource_id",
            "resource_repr",
            "before",
            "after",
            "ip_address",
            "session_id",
            "outcome",
            "notes",
        ]
    )
    for entry in (
        queryset.select_related("actor").order_by("-timestamp").iterator(chunk_size=500)
    ):
        writer.writerow(
            [
                entry.timestamp.isoformat(),
                entry.event,
                entry.actor_id or "",
                entry.actor.username if entry.actor else "",
                entry.resource_type,
                entry.resource_id,
                entry.resource_repr,
                json.dumps(entry.before) if entry.before is not None else "",
                json.dumps(entry.after) if entry.after is not None else "",
                entry.ip_address or "",
                entry.session_id,
                entry.outcome,
                entry.notes,
            ]
        )
    return response


# ---------------------------------------------------------------------------
# AuditLog admin
# ---------------------------------------------------------------------------


@admin.register(AuditLog)
class AuditLogAdmin(_LeadMentorOnly, admin.ModelAdmin):
    list_display = (
        "timestamp",
        "event",
        "actor_display",
        "resource_type",
        "resource_id",
        "resource_repr",
        "outcome",
        "ip_address",
    )
    list_filter = (
        "event",
        "outcome",
        "resource_type",
        ("timestamp", admin.DateFieldListFilter),
    )
    search_fields = (
        "actor__username",
        "actor__email",
        "resource_repr",
        "resource_id",
        "notes",
    )
    readonly_fields = (
        "timestamp",
        "actor",
        "event",
        "resource_type",
        "resource_id",
        "resource_repr",
        "before_pretty",
        "after_pretty",
        "ip_address",
        "session_id",
        "outcome",
        "notes",
    )
    ordering = ["-timestamp"]
    # Only expose the export action; never expose delete.
    actions = ["export_as_csv"]

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions

    # ------------------------------------------------------------------
    # Custom display columns
    # ------------------------------------------------------------------

    @admin.display(description="Actor")
    def actor_display(self, obj):
        if obj.actor:
            return f"{obj.actor.username} (#{obj.actor_id})"
        return "—"

    @admin.display(description="Before")
    def before_pretty(self, obj):
        if obj.before is not None:
            return format_html(
                "<pre style='margin:0'>{}</pre>", json.dumps(obj.before, indent=2)
            )
        return "—"

    @admin.display(description="After")
    def after_pretty(self, obj):
        if obj.after is not None:
            return format_html(
                "<pre style='margin:0'>{}</pre>", json.dumps(obj.after, indent=2)
            )
        return "—"

    # ------------------------------------------------------------------
    # Bulk export action
    # ------------------------------------------------------------------

    @admin.action(description="Export selected rows as CSV")
    def export_as_csv(self, request, queryset):
        return _build_csv_response(queryset)

    # ------------------------------------------------------------------
    # Filtered export URL (exports whatever the current list filters show)
    # ------------------------------------------------------------------

    def get_urls(self):
        from django.urls import path

        urls = super().get_urls()
        custom = [
            path(
                "export/",
                self.admin_site.admin_view(self._export_filtered_view),
                name="audit_auditlog_export",
            ),
        ]
        return custom + urls

    def _export_filtered_view(self, request):
        if not _is_lead_mentor(request.user):
            raise PermissionDenied
        # Reuse Django admin's changelist machinery to honour all active filters.
        changelist = self.get_changelist_instance(request)
        return _build_csv_response(changelist.get_queryset(request))

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        # Expose the export URL to the template context so we can add a button.
        extra_context["filtered_export_url"] = "export/"
        return super().changelist_view(request, extra_context=extra_context)
