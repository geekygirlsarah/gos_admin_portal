from django.contrib import admin

from orders.models import ItemTag, Order, OrderItem, ShippingCarrier, Vendor


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    fields = (
        "item_name",
        "program",
        "tag",
        "quantity",
        "unit_price",
        "url",
        "requested_by",
        "requested_at",
    )
    readonly_fields = ("requested_at",)
    show_change_link = True


@admin.register(Vendor)
class VendorAdmin(admin.ModelAdmin):
    list_display = ("name", "website", "contact_email", "contact_phone")
    search_fields = ("name", "website", "contact_email", "notes")


@admin.register(ShippingCarrier)
class ShippingCarrierAdmin(admin.ModelAdmin):
    list_display = ("name", "tracking_url_template")
    search_fields = ("name",)


@admin.register(ItemTag)
class ItemTagAdmin(admin.ModelAdmin):
    list_display = ("name", "color")
    search_fields = ("name",)


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        "pk",
        "program",
        "vendor",
        "shipping_carrier",
        "item_count",
        "total",
        "status",
        "created_by",
        "created_at",
        "ordered_at",
    )
    list_select_related = (
        "program",
        "vendor",
        "shipping_carrier",
        "created_by",
        "ordered_by",
    )
    list_filter = ("status", "program", "vendor", "shipping_carrier")
    search_fields = ("vendor_name", "notes", "tracking_number")
    readonly_fields = ("created_by", "created_at", "ordered_at", "ordered_by")
    inlines = [OrderItemInline]


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = (
        "item_name",
        "order",
        "program",
        "vendor",
        "tag",
        "quantity",
        "unit_price",
        "total",
        "requested_by",
        "requested_at",
    )
    list_select_related = ("order", "program", "vendor", "tag", "requested_by")
    list_filter = ("order__status", "program", "vendor", "tag")
    search_fields = ("item_name", "vendor_name", "notes", "url")
    readonly_fields = ("requested_by", "requested_at")
