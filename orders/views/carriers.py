from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse
from django.views.generic import CreateView, DeleteView, ListView, UpdateView

from audit.mixins import AuditedDeleteMixin
from orders.forms import ShippingCarrierForm
from orders.models import ShippingCarrier
from programs.permission_views import LeadMentorRequiredMixin

from .mixins import OrderProgramMixin


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
            f"Shipping company '{form.instance.name}' added.",
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
    LoginRequiredMixin,
    AuditedDeleteMixin,
    OrderProgramMixin,
    LeadMentorRequiredMixin,
    DeleteView,
):
    model = ShippingCarrier
    template_name = "orders/carrier_confirm_delete.html"

    def audit_snapshot(self, obj):
        return {
            "name": obj.name,
            "tracking_url_template": obj.tracking_url_template,
        }

    def form_valid(self, form):
        messages.success(
            self.request,
            f"Shipping company '{self.object.name}' deleted. Tracking numbers "
            "on existing orders will still be kept.",
        )
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("orders:carrier_list", kwargs={"program_id": self.program.id})
