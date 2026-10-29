from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.views.generic import CreateView, DeleteView, UpdateView

from audit.mixins import AuditedDeleteMixin
from orders.forms import NOT_LISTED_VENDOR, OrderItemForm
from orders.models import OrderItem
from programs.permission_views import can_user_delete
from programs.views.mixins import DynamicWritePermissionMixin

from .mixins import OrderProgramMixin


class ItemCreateView(
    LoginRequiredMixin, OrderProgramMixin, DynamicWritePermissionMixin, CreateView
):
    """Students/mentors place an individual item request."""

    model = OrderItem
    form_class = OrderItemForm
    template_name = "orders/item_form.html"
    section = "orders-request"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["program"] = self.program
        return kwargs

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

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["program"] = self.object.program or self.program
        return kwargs

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


class ItemDeleteView(
    LoginRequiredMixin, AuditedDeleteMixin, OrderProgramMixin, DeleteView
):
    model = OrderItem
    template_name = "orders/item_confirm_delete.html"

    def get_queryset(self):
        return OrderItem.objects.select_related("program")

    def audit_snapshot(self, obj):
        return {
            "item_name": obj.item_name,
            "quantity": str(obj.quantity),
            "unit_price": str(obj.unit_price) if obj.unit_price is not None else None,
            "order_id": obj.order_id,
        }

    def dispatch(self, request, *args, **kwargs):
        if not can_user_delete(request.user, "orders-request", self.get_object()):
            messages.error(
                request, "You do not have permission to delete that request."
            )
            return redirect("orders:order_list", program_id=kwargs.get("program_id"))
        return super().dispatch(request, *args, **kwargs)
