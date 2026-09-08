from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    ListView,
    UpdateView,
    View,
)

from orders.forms import (
    NOT_LISTED_VENDOR,
    ItemTagForm,
    OrderForm,
    OrderItemForm,
    ShippingCarrierForm,
    ShippingInfoForm,
    VendorForm,
)
from orders.models import (
    ItemTag,
    Order,
    OrderItem,
    ShippingCarrier,
    Vendor,
    user_display_name,
)
from programs.models import Program
from programs.permission_views import (
    LeadMentorRequiredMixin,
    can_user_delete,
    can_user_write,
    get_user_role,
    user_is_mentor_or_lead,
)
from programs.views.mixins import (
    DynamicReadPermissionMixin,
    DynamicWritePermissionMixin,
)


class OrderProgramMixin:
    """Resolves ``self.program`` and gates access on the program's feature
    toggle.

    Students/Parents only reach the orders pages when the program has the
    ``orders`` feature enabled; mentors and Lead Mentors may always access it
    (the orders list is org-wide).
    """

    def dispatch(self, request, *args, **kwargs):
        self.program = get_object_or_404(Program, pk=kwargs.get("program_id"))
        is_mentor = user_is_mentor_or_lead(request.user)
        if not self.program.features.filter(key="orders").exists() and not is_mentor:
            raise Http404("Order requests are not enabled for this program.")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["program"] = self.program
        context["user_role"] = get_user_role(self.request.user)
        return context

    def get_success_url(self):
        return reverse("orders:order_list", kwargs={"program_id": self.program.id})


class OrderStaffRequiredMixin:
    """Restricts a view to mentors and Lead Mentors (students never manage
    the grouped orders themselves)."""

    def dispatch(self, request, *args, **kwargs):
        if not user_is_mentor_or_lead(request.user):
            messages.error(request, "Only mentors and Lead Mentors can manage orders.")
            return redirect("home")
        return super().dispatch(request, *args, **kwargs)


def _groups_total(groups):
    totals = [group["total"] for group in groups if group["total"] is not None]
    if totals:
        return sum(totals, Decimal("0"))
    return None


def _group_by_program(records):
    """Group a queryset of orders or items by program, preserving first-seen
    order.

    Returns a list of ``{"program": Program|None, "items": [...], "total": Decimal|None}``.
    """
    groups = []
    index = {}
    for record in records:
        key = record.program_id or 0
        group = index.get(key)
        if group is None:
            group = {"program": record.program, "items": [], "total": None}
            index[key] = group
            groups.append(group)
        group["items"].append(record)
    for group in groups:
        totals = [record.total for record in group["items"] if record.total is not None]
        if totals:
            group["total"] = sum(totals, Decimal("0"))
    return groups


def _record_vendor_name(record):
    """Display vendor for grouping: the vendor snapshot first, then the
    linked Vendor's name, then an empty string for untagged records."""
    if isinstance(record, OrderItem):
        return record.vendor_name_display
    return record.vendor_name or (record.vendor.name if record.vendor_id else "")


def _group_by_vendor(records):
    """Group a queryset of orders or items by vendor, so mentors can batch a
    vendor's requests into a single order.

    Returns a list of ``{"vendor": str, "items": [...], "total": Decimal|None}``
    where ``vendor`` is ``""`` for records with no vendor. Groups are sorted
    alphabetically with untagged records last.
    """
    groups = []
    index = {}
    for record in records:
        key = _record_vendor_name(record)
        group = index.get(key)
        if group is None:
            group = {"vendor": key, "items": [], "total": None}
            index[key] = group
            groups.append(group)
        group["items"].append(record)
    for group in groups:
        totals = [record.total for record in group["items"] if record.total is not None]
        if totals:
            group["total"] = sum(totals, Decimal("0"))
    groups.sort(key=lambda g: (g["vendor"] == "", g["vendor"].lower()))
    return groups


def _item_export_row(item):
    order = item.order
    return {
        "program": item.program.name if item.program else "",
        "item_name": item.item_name,
        "quantity": item.quantity_normalized,
        "unit_price": item.unit_price if item.unit_price is not None else "",
        "total": item.total if item.total is not None else "",
        "url": item.url,
        "order": f"#{order.pk}" if order else "",
        "vendor": order.vendor_name if order else item.vendor_name_display,
        "vendor_url": order.vendor_url if order else item.vendor_url,
        "requested_by": item.requested_by_name,
        "requested_on": (
            timezone.localtime(item.requested_at).strftime("%Y-%m-%d %H:%M")
            if item.requested_at
            else ""
        ),
        "notes": item.notes,
        "tag": item.tag.name if item.tag_id else "",
        "status": item.get_status_display(),
        "ordered_on": (
            timezone.localtime(order.ordered_at).strftime("%Y-%m-%d %H:%M")
            if order and order.ordered_at
            else ""
        ),
    }


def _export_items_csv(items):
    import csv

    from django.http import HttpResponse

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="order_requests.csv"'
    writer = csv.writer(response)
    writer.writerow(
        [
            "Program",
            "Item",
            "Quantity",
            "Unit Price",
            "Total",
            "Link",
            "Order",
            "Vendor",
            "Vendor Website",
            "Requested By",
            "Requested On",
            "Notes",
            "Tag",
            "Status",
            "Ordered On",
        ]
    )
    for item in items.iterator(chunk_size=500):
        row = _item_export_row(item)
        writer.writerow(
            [
                row["program"],
                row["item_name"],
                row["quantity"],
                row["unit_price"],
                row["total"],
                row["url"],
                row["order"],
                row["vendor"],
                row["vendor_url"],
                row["requested_by"],
                row["requested_on"],
                row["notes"],
                row["tag"],
                row["status"],
                row["ordered_on"],
            ]
        )
    return response


def _export_items_xlsx(items):
    from io import BytesIO

    from django.http import HttpResponse
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Order Requests"
    ws.append(
        [
            "Program",
            "Item",
            "Quantity",
            "Unit Price",
            "Total",
            "Link",
            "Order",
            "Vendor",
            "Vendor Website",
            "Requested By",
            "Requested On",
            "Notes",
            "Tag",
            "Status",
            "Ordered On",
        ]
    )
    for item in items.iterator(chunk_size=500):
        row = _item_export_row(item)
        ws.append(
            [
                row["program"],
                row["item_name"],
                float(row["quantity"]) if row["quantity"] != "" else "",
                float(row["unit_price"]) if row["unit_price"] != "" else "",
                float(row["total"]) if row["total"] != "" else "",
                row["url"],
                row["order"],
                row["vendor"],
                row["vendor_url"],
                row["requested_by"],
                row["requested_on"],
                row["notes"],
                row["tag"],
                row["status"],
                row["ordered_on"],
            ]
        )
    for row in ws.iter_rows(min_row=1, max_row=ws.max_row, min_col=1, max_col=15):
        for cell in row:
            if cell.column in (2, 6, 12):
                cell.alignment = cell.alignment.copy(vertical="top")

    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    response = HttpResponse(
        buffer.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="order_requests.xlsx"'
    return response


class _OrderExportMixin:
    """Handles ``?export=csv`` / ``?export=xlsx`` downloads (Lead Mentors only).

    Views provide ``get_export_items()`` returning the item queryset to
    export (pending pool + pending orders for the list, ordered orders for the
    archive).
    """

    def _handle_export(self, items):
        export = self.request.GET.get("export")
        if not export:
            return None
        if get_user_role(self.request.user) != "LeadMentor":
            messages.error(self.request, "Only Lead Mentors can export orders.")
            return redirect("orders:order_list", program_id=self.program.id)
        if export == "csv":
            return _export_items_csv(items)
        if export == "xlsx":
            return _export_items_xlsx(items)
        return None

    def get(self, request, *args, **kwargs):
        response = self._handle_export(self.get_export_items())
        if response is not None:
            return response
        return super().get(request, *args, **kwargs)


class OrderListView(
    LoginRequiredMixin,
    OrderProgramMixin,
    _OrderExportMixin,
    DynamicReadPermissionMixin,
    ListView,
):
    """The request pool (unassigned items) plus pending grouped orders."""

    model = OrderItem
    template_name = "orders/order_list.html"
    context_object_name = "items"
    section = "orders-view"

    def get_export_items(self):
        return (
            OrderItem.objects.filter(
                Q(order__isnull=True) | ~Q(order__status=Order.STATUS_RECEIVED)
            )
            .select_related(
                "program",
                "vendor",
                "tag",
                "requested_by",
                "order__vendor",
                "order__created_by",
                "order__ordered_by",
            )
            .order_by("-requested_at")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        role = context["user_role"]
        pool_items = (
            OrderItem.objects.filter(order__isnull=True)
            .select_related("program", "vendor", "tag", "requested_by")
            .order_by("-requested_at")
        )
        active_orders = (
            Order.objects.exclude(status=Order.STATUS_RECEIVED)
            .select_related("program", "vendor", "shipping_carrier", "created_by")
            .prefetch_related("items__tag")
            .order_by("-created_at")
        )
        item_groups = _group_by_vendor(pool_items)
        order_groups = _group_by_vendor(active_orders)
        context["item_groups"] = item_groups
        context["order_groups"] = order_groups
        context["item_groups_total"] = _groups_total(item_groups)
        context["order_groups_total"] = _groups_total(order_groups)
        context["is_lead"] = role == "LeadMentor"
        context["is_staff"] = role in ("Mentor", "LeadMentor")
        context["can_request_item"] = can_user_write(
            self.request.user, "orders-request"
        )
        context["can_place_order"] = user_is_mentor_or_lead(
            self.request.user
        ) and can_user_write(self.request.user, "orders-manage")
        context["can_manage_items"] = can_user_write(self.request.user, "orders-manage")
        context["page_title"] = "Order Requests"
        return context


class OrderArchiveView(
    LoginRequiredMixin,
    OrderProgramMixin,
    _OrderExportMixin,
    DynamicReadPermissionMixin,
    ListView,
):
    """Ordered (archived) group orders, kept for budget/finance purposes."""

    model = Order
    template_name = "orders/order_archive.html"
    context_object_name = "orders"
    section = "orders-view"

    def get_queryset(self):
        return (
            Order.objects.filter(status=Order.STATUS_RECEIVED)
            .select_related(
                "program", "vendor", "shipping_carrier", "created_by", "ordered_by"
            )
            .prefetch_related("items__tag")
            .order_by("-ordered_at", "-created_at")
        )

    def get_export_items(self):
        return (
            OrderItem.objects.filter(order__status=Order.STATUS_RECEIVED)
            .select_related(
                "program",
                "vendor",
                "tag",
                "requested_by",
                "order__vendor",
                "order__created_by",
                "order__ordered_by",
            )
            .order_by("-order__ordered_at", "-requested_at")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        role = context["user_role"]
        orders = context["orders"]
        for order in orders:
            order.ordered_by_name = user_display_name(order.ordered_by)
        context["order_groups"] = _group_by_program(orders)
        context["is_lead"] = role == "LeadMentor"
        context["is_staff"] = role in ("Mentor", "LeadMentor")
        context["page_title"] = "Order Archive"
        return context


class OrderDetailView(
    LoginRequiredMixin,
    OrderProgramMixin,
    DynamicReadPermissionMixin,
    DetailView,
):
    """A single grouped order with its items and status.

    Readable by anyone with read access to the orders section (students can
    follow the status of items they requested); mutating actions are gated on
    the granular order permissions per object (managing items, editing
    shipping).
    """

    model = Order
    template_name = "orders/order_detail.html"
    context_object_name = "order"
    section = "orders-view"

    def get_queryset(self):
        return Order.objects.select_related(
            "program", "vendor", "shipping_carrier", "created_by", "ordered_by"
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        role = context["user_role"]
        context["items"] = self.object.items.select_related(
            "program", "vendor", "tag", "requested_by", "order"
        ).all()
        context["available_items"] = (
            OrderItem.objects.filter(order__isnull=True)
            .select_related("program", "vendor", "tag", "requested_by")
            .order_by("vendor_name", "vendor__name", "-requested_at")
        )
        context["is_lead"] = role == "LeadMentor"
        context["can_manage_items"] = can_user_write(
            self.request.user, "orders-manage", self.object
        )
        context["can_edit_shipping"] = can_user_write(
            self.request.user, "orders-shipping", self.object
        )
        context["shipping_form"] = ShippingInfoForm(instance=self.object)
        context["not_listed_vendor"] = NOT_LISTED_VENDOR
        context["page_title"] = f"Order #{self.object.pk}"
        return context


class OrderCreateView(
    LoginRequiredMixin,
    OrderStaffRequiredMixin,
    OrderProgramMixin,
    DynamicWritePermissionMixin,
    CreateView,
):
    """Mentor/Lead Mentor groups requested items into a new order."""

    model = Order
    form_class = OrderForm
    template_name = "orders/order_form.html"
    section = "orders-manage"

    def get_initial(self):
        initial = super().get_initial()
        initial["program"] = self.program
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["not_listed_vendor"] = NOT_LISTED_VENDOR
        return context

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        messages.success(
            self.request,
            "Order created. Add items from the detail page or mark it ordered when you're ready.",
        )
        return super().form_valid(form)

    def get_success_url(self):
        return reverse(
            "orders:order_detail",
            kwargs={"program_id": self.program.id, "pk": self.object.pk},
        )


class OrderUpdateView(
    LoginRequiredMixin,
    OrderStaffRequiredMixin,
    OrderProgramMixin,
    DynamicWritePermissionMixin,
    UpdateView,
):
    """Edit a grouped order's program/vendor/notes (pending orders for mentors;
    anything for Lead Mentors)."""

    model = Order
    form_class = OrderForm
    template_name = "orders/order_form.html"
    section = "orders-manage"

    def get_queryset(self):
        # Org-wide queryset; creator-only editing is enforced by
        # ``can_user_write('orders-manage', obj)`` for non-Lead-Mentors.
        return Order.objects.select_related("program", "vendor")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["not_listed_vendor"] = NOT_LISTED_VENDOR
        return context

    def form_valid(self, form):
        messages.success(self.request, "Order updated.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse(
            "orders:order_detail",
            kwargs={"program_id": self.program.id, "pk": self.object.pk},
        )


class OrderDeleteView(
    LoginRequiredMixin, OrderStaffRequiredMixin, OrderProgramMixin, DeleteView
):
    model = Order
    template_name = "orders/order_confirm_delete.html"

    def get_queryset(self):
        return Order.objects.all()

    def dispatch(self, request, *args, **kwargs):
        if not can_user_delete(request.user, "orders-manage", self.get_object()):
            messages.error(request, "You do not have permission to delete that order.")
            return redirect("orders:order_list", program_id=kwargs.get("program_id"))
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        # Return grouped items to the request pool rather than deleting them.
        self.object.items.update(order=None)
        if self.object.vendor_name:
            messages.success(
                self.request,
                f"Order with '{self.object.vendor_name}' deleted; its items "
                "were returned to the requests list.",
            )
        else:
            messages.success(
                self.request,
                "Order deleted; its items were returned to the requests list.",
            )
        return super().form_valid(form)


class OrderMarkOrderedView(
    LoginRequiredMixin, OrderProgramMixin, LeadMentorRequiredMixin, View
):
    """Lead Mentor marks a pending order as placed, or un-marks a shipped order
    back to ordered. Either way the order stays on the active list (it is only
    archived once received)."""

    def post(self, request, program_id, pk):
        order = get_object_or_404(Order, pk=pk)
        if order.status == Order.STATUS_PENDING:
            order.status = Order.STATUS_ORDERED
            order.ordered_at = timezone.now()
            order.ordered_by = request.user
            order.save(update_fields=["status", "ordered_at", "ordered_by"])
            messages.success(
                request,
                f"Order {order} marked as ordered.",
            )
            return redirect("orders:order_list", program_id=self.program.id)
        if order.status == Order.STATUS_SHIPPED:
            order.status = Order.STATUS_ORDERED
            order.shipped_date = None
            order.save(update_fields=["status", "shipped_date"])
            messages.success(
                request,
                f"Order {order} un-marked as shipped.",
            )
            return redirect("orders:order_list", program_id=self.program.id)
        messages.info(request, "That order has already been marked as ordered.")
        return redirect("orders:order_list", program_id=self.program.id)


class OrderMarkPendingView(
    LoginRequiredMixin, OrderProgramMixin, LeadMentorRequiredMixin, View
):
    """Reopens an order (undoes 'mark ordered'/'mark shipped'/'mark received')."""

    def post(self, request, program_id, pk):
        order = get_object_or_404(Order, pk=pk)
        if order.status in (
            Order.STATUS_ORDERED,
            Order.STATUS_SHIPPED,
            Order.STATUS_RECEIVED,
        ):
            order.status = Order.STATUS_PENDING
            order.ordered_at = None
            order.ordered_by = None
            order.save(update_fields=["status", "ordered_at", "ordered_by"])
            messages.success(
                request,
                f"Order {order} moved back to the pending list.",
            )
            return redirect("orders:order_archive", program_id=self.program.id)
        if order.status == Order.STATUS_PENDING:
            messages.info(request, "That order is already pending.")
        else:
            messages.info(request, "That order is not archived.")
        return redirect("orders:order_list", program_id=self.program.id)


class OrderMarkShippedView(
    LoginRequiredMixin, OrderProgramMixin, LeadMentorRequiredMixin, View
):
    """Lead Mentor marks an ordered order as shipped. The order stays on the
    active list until it's received."""

    def post(self, request, program_id, pk):
        order = get_object_or_404(Order, pk=pk)
        if order.status == Order.STATUS_ORDERED:
            order.status = Order.STATUS_SHIPPED
            order.save(update_fields=["status"])
            messages.success(
                request,
                f"Order {order} marked as shipped. Add tracking details when you have them.",
            )
        else:
            messages.info(request, "Only ordered orders can be marked as shipped.")
        return redirect("orders:order_list", program_id=self.program.id)


class OrderMarkReceivedView(
    LoginRequiredMixin, OrderProgramMixin, LeadMentorRequiredMixin, View
):
    """Lead Mentor marks a shipped order as received, moving it to the archive.

    Orders are only archived once they've been received; placed/shipped orders
    stay on the active list so shipping/tracking details can still be edited.
    """

    def post(self, request, program_id, pk):
        order = get_object_or_404(Order, pk=pk)
        if order.status == Order.STATUS_SHIPPED:
            order.status = Order.STATUS_RECEIVED
            order.save(update_fields=["status"])
            messages.success(
                request,
                f"Order {order} marked as received and moved to the archive.",
            )
        else:
            messages.info(request, "Only shipped orders can be marked as received.")
        return redirect("orders:order_archive", program_id=self.program.id)


class OrderShippingUpdateView(
    LoginRequiredMixin,
    OrderProgramMixin,
    DynamicWritePermissionMixin,
    View,
):
    """Saves shipping/tracking details for a placed order from the detail page."""

    section = "orders-shipping"

    def get_object(self):
        return get_object_or_404(Order, pk=self.kwargs.get("pk"))

    def post(self, request, program_id, pk):
        order = self.get_object()
        if order.status not in (Order.STATUS_ORDERED, Order.STATUS_SHIPPED):
            messages.error(
                request, "Shipping details can only be edited on active orders."
            )
            return redirect(
                "orders:order_detail",
                program_id=self.program.id,
                pk=order.pk,
            )
        form = ShippingInfoForm(request.POST, instance=order)
        if form.is_valid():
            form.save()
            messages.success(request, "Shipping details updated.")
        else:
            messages.error(request, "Please check the shipping details.")
        return redirect(
            "orders:order_detail",
            program_id=self.program.id,
            pk=order.pk,
        )


class OrderAddItemsView(
    LoginRequiredMixin,
    OrderStaffRequiredMixin,
    OrderProgramMixin,
    DynamicWritePermissionMixin,
    View,
):
    """Assigns selected unassigned requests to a pending order."""

    section = "orders-manage"

    def get_object(self):
        return get_object_or_404(Order, pk=self.kwargs.get("pk"))

    def post(self, request, program_id, pk):
        order = self.get_object()
        if order.status != Order.STATUS_PENDING:
            messages.error(request, "Only pending orders can accept more items.")
            return redirect(
                "orders:order_detail",
                program_id=self.program.id,
                pk=order.pk,
            )
        selected = request.POST.getlist("items")
        items = OrderItem.objects.filter(pk__in=selected, order__isnull=True)
        count = items.update(order=order)
        if count:
            messages.success(self.request, f"{count} item(s) added to the order.")
        else:
            messages.info(self.request, "No unassigned items were selected.")
        return redirect("orders:order_detail", program_id=self.program.id, pk=order.pk)


class OrderRemoveItemView(
    LoginRequiredMixin,
    OrderStaffRequiredMixin,
    OrderProgramMixin,
    DynamicWritePermissionMixin,
    View,
):
    """Moves a single item back from a pending order to the request pool."""

    section = "orders-manage"

    def get_object(self):
        return get_object_or_404(Order, pk=self.kwargs.get("pk"))

    def post(self, request, program_id, pk, item_id):
        order = self.get_object()
        if order.status != Order.STATUS_PENDING:
            messages.error(request, "Items can only be removed from pending orders.")
            return redirect(
                "orders:order_detail",
                program_id=self.program.id,
                pk=order.pk,
            )
        item = get_object_or_404(OrderItem, pk=item_id, order=order)
        item.order = None
        item.save(update_fields=["order"])
        messages.success(
            self.request,
            f"'{item.item_name}' returned to the requests list.",
        )
        return redirect("orders:order_detail", program_id=self.program.id, pk=order.pk)


class ItemCreateView(
    LoginRequiredMixin, OrderProgramMixin, DynamicWritePermissionMixin, CreateView
):
    """Students/mentors place an individual item request."""

    model = OrderItem
    form_class = OrderItemForm
    template_name = "orders/item_form.html"
    section = "orders-request"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["not_listed_vendor"] = NOT_LISTED_VENDOR
        return context

    def form_valid(self, form):
        form.instance.requested_by = self.request.user
        form.instance.program = self.program
        messages.success(
            self.request,
            "Item added to the requests list. A mentor will group it into an order.",
        )
        return super().form_valid(form)


class ItemUpdateView(
    LoginRequiredMixin, OrderProgramMixin, DynamicWritePermissionMixin, UpdateView
):
    model = OrderItem
    form_class = OrderItemForm
    template_name = "orders/item_form.html"
    section = "orders-request"

    def get_queryset(self):
        # Org-wide queryset; creator/unassigned editing is enforced by
        # ``can_user_write('orders-request', obj)`` for non-Lead-Mentors.
        return OrderItem.objects.select_related("program", "vendor")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["not_listed_vendor"] = NOT_LISTED_VENDOR
        return context

    def form_valid(self, form):
        messages.success(self.request, "Request updated.")
        return super().form_valid(form)


class ItemDeleteView(LoginRequiredMixin, OrderProgramMixin, DeleteView):
    model = OrderItem
    template_name = "orders/item_confirm_delete.html"

    def get_queryset(self):
        return OrderItem.objects.select_related("program")

    def dispatch(self, request, *args, **kwargs):
        if not can_user_delete(request.user, "orders-request", self.get_object()):
            messages.error(
                request, "You do not have permission to delete that request."
            )
            return redirect("orders:order_list", program_id=kwargs.get("program_id"))
        return super().dispatch(request, *args, **kwargs)


class VendorListView(
    LoginRequiredMixin, OrderProgramMixin, LeadMentorRequiredMixin, ListView
):
    """Org-wide vendor reference list (Lead Mentor managed)."""

    model = Vendor
    template_name = "orders/vendor_list.html"
    context_object_name = "vendors"

    def get_queryset(self):
        return Vendor.objects.order_by("name")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["page_title"] = "Vendors"
        return context


class VendorCreateView(
    LoginRequiredMixin, OrderProgramMixin, LeadMentorRequiredMixin, CreateView
):
    model = Vendor
    form_class = VendorForm
    template_name = "orders/vendor_form.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["page_title"] = "Add Vendor"
        return context

    def form_valid(self, form):
        messages.success(
            self.request,
            f"Vendor '{form.instance.name}' added. Mentors can now pick it when "
            "grouping orders.",
        )
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("orders:vendor_list", kwargs={"program_id": self.program.id})


class VendorUpdateView(
    LoginRequiredMixin, OrderProgramMixin, LeadMentorRequiredMixin, UpdateView
):
    model = Vendor
    form_class = VendorForm
    template_name = "orders/vendor_form.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["page_title"] = "Edit Vendor"
        return context

    def form_valid(self, form):
        messages.success(self.request, f"Vendor '{form.instance.name}' updated.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("orders:vendor_list", kwargs={"program_id": self.program.id})


class VendorDeleteView(
    LoginRequiredMixin, OrderProgramMixin, LeadMentorRequiredMixin, DeleteView
):
    model = Vendor
    template_name = "orders/vendor_confirm_delete.html"

    def form_valid(self, form):
        name = self.object.name
        messages.success(
            self.request,
            f"Vendor '{name}' deleted. Existing orders keep any vendor name "
            "they already had.",
        )
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("orders:vendor_list", kwargs={"program_id": self.program.id})


class ShippingCarrierListView(
    LoginRequiredMixin, OrderProgramMixin, LeadMentorRequiredMixin, ListView
):
    """Org-wide shipping companies reference list (Lead Mentor managed)."""

    model = ShippingCarrier
    template_name = "orders/carrier_list.html"
    context_object_name = "carriers"

    def get_queryset(self):
        return ShippingCarrier.objects.order_by("name")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["page_title"] = "Shipping companies"
        return context


class ShippingCarrierCreateView(
    LoginRequiredMixin, OrderProgramMixin, LeadMentorRequiredMixin, CreateView
):
    model = ShippingCarrier
    form_class = ShippingCarrierForm
    template_name = "orders/carrier_form.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["page_title"] = "Add Shipping Company"
        return context

    def form_valid(self, form):
        messages.success(
            self.request,
            f"Shipping company '{form.instance.name}' added. Mentors can now "
            "pick it when recording tracking info.",
        )
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("orders:carrier_list", kwargs={"program_id": self.program.id})


class ShippingCarrierUpdateView(
    LoginRequiredMixin, OrderProgramMixin, LeadMentorRequiredMixin, UpdateView
):
    model = ShippingCarrier
    form_class = ShippingCarrierForm
    template_name = "orders/carrier_form.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["page_title"] = "Edit Shipping Company"
        return context

    def form_valid(self, form):
        messages.success(
            self.request, f"Shipping company '{form.instance.name}' updated."
        )
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("orders:carrier_list", kwargs={"program_id": self.program.id})


class ShippingCarrierDeleteView(
    LoginRequiredMixin, OrderProgramMixin, LeadMentorRequiredMixin, DeleteView
):
    model = ShippingCarrier
    template_name = "orders/carrier_confirm_delete.html"

    def form_valid(self, form):
        name = self.object.name
        messages.success(
            self.request,
            f"Shipping company '{name}' deleted. Orders that used it keep their "
            "tracking info but lose the clickable link.",
        )
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("orders:carrier_list", kwargs={"program_id": self.program.id})


class ItemTagListView(
    LoginRequiredMixin, OrderProgramMixin, LeadMentorRequiredMixin, ListView
):
    """Org-wide item tags reference list (Lead Mentor managed)."""

    model = ItemTag
    template_name = "orders/tag_list.html"
    context_object_name = "tags"

    def get_queryset(self):
        return ItemTag.objects.order_by("name")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["page_title"] = "Item tags"
        return context


class ItemTagCreateView(
    LoginRequiredMixin, OrderProgramMixin, LeadMentorRequiredMixin, CreateView
):
    model = ItemTag
    form_class = ItemTagForm
    template_name = "orders/tag_form.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["page_title"] = "Add Item Tag"
        return context

    def form_valid(self, form):
        messages.success(
            self.request,
            f"Tag '{form.instance.name}' added. Students and mentors can now "
            "apply it to item requests.",
        )
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("orders:tag_list", kwargs={"program_id": self.program.id})


class ItemTagUpdateView(
    LoginRequiredMixin, OrderProgramMixin, LeadMentorRequiredMixin, UpdateView
):
    model = ItemTag
    form_class = ItemTagForm
    template_name = "orders/tag_form.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["page_title"] = "Edit Item Tag"
        return context

    def form_valid(self, form):
        messages.success(self.request, f"Tag '{form.instance.name}' updated.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("orders:tag_list", kwargs={"program_id": self.program.id})


class ItemTagDeleteView(
    LoginRequiredMixin, OrderProgramMixin, LeadMentorRequiredMixin, DeleteView
):
    model = ItemTag
    template_name = "orders/tag_confirm_delete.html"

    def form_valid(self, form):
        name = self.object.name
        messages.success(
            self.request,
            f"Tag '{name}' deleted. Items that had it keep their requests but "
            "no longer show the tag.",
        )
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("orders:tag_list", kwargs={"program_id": self.program.id})
