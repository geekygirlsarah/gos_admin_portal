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

from orders.forms import NOT_LISTED_VENDOR, OrderForm, OrderItemForm, VendorForm
from orders.models import Order, OrderItem, Vendor, user_display_name
from programs.models import Program
from programs.permission_views import (
    LeadMentorRequiredMixin,
    can_user_delete,
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
        "vendor": order.vendor_name if order else "",
        "vendor_url": order.vendor_url if order else "",
        "requested_by": item.requested_by_name,
        "requested_on": (
            timezone.localtime(item.requested_at).strftime("%Y-%m-%d %H:%M")
            if item.requested_at
            else ""
        ),
        "notes": item.notes,
        "status": order.get_status_display() if order else "Pending",
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
                row["status"],
                row["ordered_on"],
            ]
        )
    for row in ws.iter_rows(min_row=1, max_row=ws.max_row, min_col=1, max_col=14):
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
    section = "orders"

    def get_export_items(self):
        return (
            OrderItem.objects.filter(
                Q(order__isnull=True) | Q(order__status=Order.STATUS_PENDING)
            )
            .select_related(
                "program",
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
            .select_related("program", "requested_by")
            .order_by("-requested_at")
        )
        pending_orders = (
            Order.objects.filter(status=Order.STATUS_PENDING)
            .select_related("program", "vendor", "created_by")
            .prefetch_related("items")
            .order_by("-created_at")
        )
        item_groups = _group_by_program(pool_items)
        order_groups = _group_by_program(pending_orders)
        context["item_groups"] = item_groups
        context["order_groups"] = order_groups
        context["item_groups_total"] = _groups_total(item_groups)
        context["order_groups_total"] = _groups_total(order_groups)
        context["is_lead"] = role == "LeadMentor"
        context["is_staff"] = role in ("Mentor", "LeadMentor")
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
    section = "orders"

    def get_queryset(self):
        return (
            Order.objects.filter(status=Order.STATUS_ORDERED)
            .select_related("program", "vendor", "created_by", "ordered_by")
            .prefetch_related("items")
            .order_by("-ordered_at", "-created_at")
        )

    def get_export_items(self):
        return (
            OrderItem.objects.filter(order__status=Order.STATUS_ORDERED)
            .select_related(
                "program",
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
    OrderStaffRequiredMixin,
    OrderProgramMixin,
    DynamicWritePermissionMixin,
    DetailView,
):
    """A single grouped order with its items (mentors/Lead Mentors only)."""

    model = Order
    template_name = "orders/order_detail.html"
    context_object_name = "order"
    section = "orders"

    def get_queryset(self):
        return Order.objects.select_related(
            "program", "vendor", "created_by", "ordered_by"
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        role = context["user_role"]
        context["items"] = self.object.items.select_related(
            "program", "requested_by"
        ).all()
        context["available_items"] = (
            OrderItem.objects.filter(order__isnull=True)
            .select_related("program", "requested_by")
            .order_by("program__name", "-requested_at")
        )
        context["is_lead"] = role == "LeadMentor"
        context["can_manage_items"] = self.object.status == Order.STATUS_PENDING and (
            context["is_lead"] or self.object.created_by_id == self.request.user.id
        )
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
    section = "orders"

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
    section = "orders"

    def get_queryset(self):
        # Org-wide queryset; creator-only editing is enforced by
        # ``can_user_write('orders', obj)`` for non-Lead-Mentors.
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
        if not can_user_delete(request.user, "orders", self.get_object()):
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
    """Lead Mentor marks a pending order as placed (moves it to the archive)."""

    def post(self, request, program_id, pk):
        order = get_object_or_404(Order, pk=pk)
        if order.status == Order.STATUS_PENDING:
            order.status = Order.STATUS_ORDERED
            order.ordered_at = timezone.now()
            order.ordered_by = request.user
            order.save(update_fields=["status", "ordered_at", "ordered_by"])
            messages.success(
                request,
                f"Order {order} marked as ordered and moved to the archive.",
            )
        else:
            messages.info(request, "That order has already been marked as ordered.")
        return redirect("orders:order_list", program_id=self.program.id)


class OrderMarkPendingView(
    LoginRequiredMixin, OrderProgramMixin, LeadMentorRequiredMixin, View
):
    """Reopens an archived order (undoes 'mark ordered')."""

    def post(self, request, program_id, pk):
        order = get_object_or_404(Order, pk=pk)
        if order.status == Order.STATUS_ORDERED:
            order.status = Order.STATUS_PENDING
            order.ordered_at = None
            order.ordered_by = None
            order.save(update_fields=["status", "ordered_at", "ordered_by"])
            messages.success(
                request,
                f"Order {order} moved back to the pending list.",
            )
        else:
            messages.info(request, "That order is not archived.")
        return redirect("orders:order_archive", program_id=self.program.id)


class OrderAddItemsView(
    LoginRequiredMixin,
    OrderStaffRequiredMixin,
    OrderProgramMixin,
    DynamicWritePermissionMixin,
    View,
):
    """Assigns selected unassigned requests to a pending order."""

    section = "orders"

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

    section = "orders"

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
    section = "orders"

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
    section = "orders"

    def get_queryset(self):
        # Org-wide queryset; creator/unassigned editing is enforced by
        # ``can_user_write('orders', obj)`` for non-Lead-Mentors.
        return OrderItem.objects.select_related("program")

    def form_valid(self, form):
        messages.success(self.request, "Request updated.")
        return super().form_valid(form)


class ItemDeleteView(LoginRequiredMixin, OrderProgramMixin, DeleteView):
    model = OrderItem
    template_name = "orders/item_confirm_delete.html"

    def get_queryset(self):
        return OrderItem.objects.select_related("program")

    def dispatch(self, request, *args, **kwargs):
        if not can_user_delete(request.user, "orders", self.get_object()):
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
