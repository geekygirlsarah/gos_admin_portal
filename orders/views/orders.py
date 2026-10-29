from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
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

from audit.mixins import AuditedDeleteMixin
from orders.forms import (
    NOT_LISTED_VENDOR,
    OrderForm,
    ShippingInfoForm,
)
from orders.models import (
    Order,
    OrderItem,
    user_display_name,
)
from programs.permission_views import (
    LeadMentorRequiredMixin,
    can_user_delete,
    can_user_write,
    user_is_mentor_or_lead,
)
from programs.views.mixins import (
    DynamicReadPermissionMixin,
    DynamicWritePermissionMixin,
)

from .exports import _OrderExportMixin
from .mixins import (
    OrderProgramMixin,
    OrderStaffRequiredMixin,
    _group_by_program,
    _group_by_vendor,
    _groups_total,
)


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
                "team",
                "crew",
                "subteam",
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
        pool_items = list(
            OrderItem.objects.filter(order__isnull=True)
            .select_related(
                "program", "vendor", "team", "crew", "subteam", "requested_by"
            )
            .order_by("-requested_at")
        )
        active_orders = list(
            Order.objects.exclude(status=Order.STATUS_RECEIVED)
            .select_related("program", "vendor", "shipping_carrier", "created_by")
            .prefetch_related("items__team", "items__crew", "items__subteam")
            .order_by("-created_at")
        )
        item_groups = _group_by_vendor(pool_items)
        order_groups = _group_by_vendor(active_orders)
        item_groups_total = _groups_total(item_groups)
        order_groups_total = _groups_total(order_groups)

        pool_items_count = len(pool_items)
        in_progress_orders = [
            o
            for o in active_orders
            if o.status in (Order.STATUS_PENDING, Order.STATUS_ORDERED)
        ]
        in_progress_orders_count = len(in_progress_orders)
        shipped_orders = [o for o in active_orders if o.status == Order.STATUS_SHIPPED]
        shipped_orders_count = len(shipped_orders)

        context["pool_items_count"] = pool_items_count
        context["in_progress_orders_count"] = in_progress_orders_count
        context["shipped_orders_count"] = shipped_orders_count
        context["active_tab"] = "active"
        context["item_groups"] = item_groups
        context["order_groups"] = order_groups
        context["item_groups_total"] = item_groups_total
        context["order_groups_total"] = order_groups_total
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
            .prefetch_related("items__team", "items__crew", "items__subteam")
            .order_by("-ordered_at", "-created_at")
        )

    def get_export_items(self):
        return (
            OrderItem.objects.filter(order__status=Order.STATUS_RECEIVED)
            .select_related(
                "program",
                "vendor",
                "team",
                "crew",
                "subteam",
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
        orders = list(context["orders"])
        for order in orders:
            order.ordered_by_name = user_display_name(order.ordered_by)
        order_groups = _group_by_program(orders)
        context["order_groups"] = order_groups
        context["archive_total"] = _groups_total(order_groups)
        context["archive_orders_count"] = len(orders)
        context["active_tab"] = "archive"
        context["is_lead"] = role == "LeadMentor"
        context["is_staff"] = role in ("Mentor", "LeadMentor")
        context["can_request_item"] = can_user_write(
            self.request.user, "orders-request"
        )
        context["can_place_order"] = user_is_mentor_or_lead(
            self.request.user
        ) and can_user_write(self.request.user, "orders-manage")
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
            "program", "vendor", "team", "crew", "subteam", "requested_by", "order"
        ).all()
        context["available_items"] = (
            OrderItem.objects.filter(order__isnull=True)
            .select_related(
                "program", "vendor", "team", "crew", "subteam", "requested_by"
            )
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
    LoginRequiredMixin,
    AuditedDeleteMixin,
    OrderStaffRequiredMixin,
    OrderProgramMixin,
    DeleteView,
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

    def audit_snapshot(self, obj):
        return {
            "vendor_name": obj.vendor_name,
            "status": obj.status,
            "total": str(obj.total) if obj.total is not None else None,
            "item_count": obj.item_count,
        }

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

    @staticmethod
    def _parse_optional_cost(raw_val):
        if not raw_val:
            return None, True
        val = raw_val.strip()
        if not val:
            return None, True
        try:
            cost = Decimal(val)
            if cost >= Decimal("0"):
                return cost, True
        except (InvalidOperation, TypeError, ValueError):
            pass
        return None, False

    def _mark_as_ordered(self, request, order):
        order.status = Order.STATUS_ORDERED
        order.ordered_at = timezone.now()
        order.ordered_by = request.user
        update_fields = ["status", "ordered_at", "ordered_by"]

        for field in ("shipping_cost", "tax"):
            if field in request.POST:
                val, valid = self._parse_optional_cost(request.POST.get(field))
                if valid:
                    setattr(order, field, val)
                    update_fields.append(field)

        order.save(update_fields=update_fields)
        messages.success(
            request,
            f"Order {order} marked as ordered.",
        )
        return redirect("orders:order_list", program_id=self.program.id)

    def post(self, request, program_id, pk):
        order = get_object_or_404(Order, pk=pk)
        if order.status == Order.STATUS_PENDING:
            return self._mark_as_ordered(request, order)
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
