from django.contrib import admin

from orders.models import PurchaseOrder


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(admin.ModelAdmin):
    list_display = (
        "item_name",
        "program",
        "quantity",
        "unit_price",
        "total",
        "status",
        "requested_by_name",
        "created_at",
        "ordered_at",
    )
    list_select_related = ("program", "created_by", "ordered_by")
    list_filter = ("status", "program")
    search_fields = ("item_name", "notes", "url")
    readonly_fields = ("created_by", "created_at", "ordered_at", "ordered_by")
