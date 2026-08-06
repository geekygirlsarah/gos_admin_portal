from django.contrib.auth.mixins import (
    LoginRequiredMixin,
    PermissionRequiredMixin,
)
from django.db.models.functions import Lower
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.generic import CreateView, ListView, UpdateView

from ..forms import SchoolForm
from ..models import School
from ..utils import get_safe_url
from .mixins import LogFormSaveMixin, SortableListViewMixin


class SchoolListView(LoginRequiredMixin, SortableListViewMixin, ListView):
    model = School
    template_name = "schools/list.html"
    context_object_name = "schools"

    sort_fields = {
        "name": Lower("name"),
        "district": Lower("district"),
        "city": Lower("city"),
        "state": "state",
    }
    default_sort_field = "name"

    def get_queryset(self):
        return self.apply_sorting(super().get_queryset())


class SchoolCreateView(
    LogFormSaveMixin, LoginRequiredMixin, PermissionRequiredMixin, CreateView
):
    model = School
    form_class = SchoolForm
    template_name = "schools/form.html"
    permission_required = "programs.add_school"

    def get_success_url(self):
        # After creating a School, return to the Schools listing
        return reverse("school_list")


class SchoolUpdateView(
    LogFormSaveMixin, LoginRequiredMixin, PermissionRequiredMixin, UpdateView
):
    model = School
    form_class = SchoolForm
    template_name = "schools/form.html"
    permission_required = "programs.change_school"

    def get_success_url(self):
        next_url = self.request.GET.get("next")
        safe_url = get_safe_url(self.request, next_url)
        if safe_url:
            return safe_url
        return reverse("school_edit", args=[self.object.pk])
