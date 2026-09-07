from django.contrib import admin

from orders.models import PurchaseOrder, Vendor


@admin.register(Vendor)
class VendorAdmin(admin.ModelAdmin):
    list_display = ("name", "website", "contact_email", "contact_phone")
    search_fields = ("name", "website", "contact_email", "notes")


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(admin.ModelAdmin):
    list_display = (
        "item_name",
        "program",
        "vendor",
        "quantity",
        "unit_price",
        "total",
        "status",
        "requested_by_name",
        "created_at",
        "ordered_at",
    )
    list_select_related = ("program", "created_by", "ordered_by", "vendor")
    list_filter = ("status", "program", "vendor")
    search_fields = ("item_name", "notes", "url", "vendor_name")
    readonly_fields = ("created_by", "created_at", "ordered_at", "ordered_by")
