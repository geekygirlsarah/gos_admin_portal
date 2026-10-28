from decimal import Decimal

from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse

from programs.models import Program
from programs.permission_views import (
    get_user_role,
    user_is_mentor_or_lead,
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
    return (
        record.vendor_name or (record.vendor.name if record.vendor else "") or ""
    ).strip()


def _group_by_vendor(records):
    """Group a queryset of orders or items by vendor, so mentors can batch a
    vendor's requests into a single order.

    Returns a list of ``{"vendor": str, "items": [...], "total": Decimal|None}`` sorted
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
