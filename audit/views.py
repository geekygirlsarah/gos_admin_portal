from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django.views.generic import ListView

from programs.permission_views import LeadMentorRequiredMixin

from .forms import OrganizationAuditFilterForm
from .models import AuditLog


@method_decorator(never_cache, name="dispatch")
class OrganizationAuditLogView(LeadMentorRequiredMixin, ListView):
    template_name = "audit/organization_log.html"
    context_object_name = "logs"
    paginate_by = OrganizationAuditFilterForm.DEFAULT_PAGE_SIZE

    def get_queryset(self):
        organization = getattr(self.request, "organization", None)
        if organization is None:
            raise PermissionDenied
        filters = self.request.GET.copy()
        if "page_size" not in filters:
            filters["page_size"] = str(self.paginate_by)
        self.filter_form = OrganizationAuditFilterForm(filters)
        queryset = AuditLog.objects.filter(organization=organization).select_related(
            "actor"
        )
        if not self.filter_form.is_valid():
            return queryset.none()
        data = self.filter_form.cleaned_data
        if data["search"]:
            search = data["search"]
            queryset = queryset.filter(
                Q(actor__username__icontains=search)
                | Q(actor__first_name__icontains=search)
                | Q(actor__last_name__icontains=search)
                | Q(actor__email__icontains=search)
                | Q(resource_repr__icontains=search)
                | Q(notes__icontains=search)
            )
        if data["event"]:
            queryset = queryset.filter(event=data["event"])
        if data["outcome"]:
            queryset = queryset.filter(outcome=data["outcome"])
        if data["date_from"]:
            queryset = queryset.filter(timestamp__date__gte=data["date_from"])
        if data["date_to"]:
            queryset = queryset.filter(timestamp__date__lte=data["date_to"])
        return queryset.order_by("-timestamp", "-pk")

    def get_paginate_by(self, queryset):
        return self.filter_form.cleaned_data.get("page_size", self.paginate_by)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["filter_form"] = self.filter_form
        query = self.request.GET.copy()
        query.pop("page", None)
        context["filter_query"] = query.urlencode()
        return context
