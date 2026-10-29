from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse
from django.views.generic import CreateView, DeleteView, ListView, UpdateView

from audit.mixins import AuditedDeleteMixin
from orders.forms import VendorForm
from orders.models import Vendor
from programs.permission_views import LeadMentorRequiredMixin

from .mixins import OrderProgramMixin


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
    LoginRequiredMixin,
    AuditedDeleteMixin,
    OrderProgramMixin,
    LeadMentorRequiredMixin,
    DeleteView,
):
    model = Vendor
    template_name = "orders/vendor_confirm_delete.html"

    def audit_snapshot(self, obj):
        return {"name": obj.name, "website": obj.website}

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
