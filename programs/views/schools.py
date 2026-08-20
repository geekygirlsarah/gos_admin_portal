from django.contrib import messages
from django.contrib.auth.mixins import (
    LoginRequiredMixin,
    PermissionRequiredMixin,
)
from django.db import transaction
from django.db.models import Count
from django.db.models.functions import Lower
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.views.generic import CreateView, FormView, ListView, UpdateView, View

from ..forms import SchoolDistrictForm, SchoolForm, SchoolMergeForm
from ..models import School, SchoolDistrict, Student
from ..utils import get_safe_url
from .mixins import LogFormSaveMixin, SortableListViewMixin


class SchoolListView(LoginRequiredMixin, SortableListViewMixin, ListView):
    model = School
    template_name = "schools/list.html"
    context_object_name = "schools"

    sort_fields = {
        "name": Lower("name"),
        "district": Lower("district__name"),
        "city": Lower("city"),
        "state": "state",
    }
    default_sort_field = "name"

    def get_queryset(self):
        return self.apply_sorting(super().get_queryset().select_related("district"))


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


class SchoolMergeView(LoginRequiredMixin, PermissionRequiredMixin, FormView):
    """Merge one school into another, reassigning students and preserving
    contact data.

    The ``keep`` school survives and keeps any address/contact fields it
    already has. Missing fields are filled from the ``source`` school, which is
    then deleted.
    """

    form_class = SchoolMergeForm
    template_name = "schools/merge.html"
    permission_required = "programs.change_school"
    success_url = reverse_lazy("school_list")

    def get_initial(self):
        initial = super().get_initial()
        source_pk = self.request.GET.get("source")
        if source_pk:
            try:
                initial["source"] = int(source_pk)
            except (TypeError, ValueError):
                pass
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["schools"] = list(School.objects.all())
        return context

    def form_valid(self, form):
        keep = form.cleaned_data["keep"]
        source = form.cleaned_data["source"]
        with transaction.atomic():
            contact_fields = [
                "district",
                "street_address",
                "city",
                "state",
                "zip_code",
            ]
            changed = False
            for field in contact_fields:
                if not getattr(keep, field) and getattr(source, field):
                    setattr(keep, field, getattr(source, field))
                    changed = True
            if changed:
                keep.save()
            Student.objects.filter(school=source).update(school=keep)
            source.delete()
        messages.success(
            self.request,
            f'Merged "{source.name}" into "{keep.name}". '
            f"All students were moved to {keep.name}.",
        )
        return super().form_valid(form)


class DistrictListView(LoginRequiredMixin, SortableListViewMixin, ListView):
    model = SchoolDistrict
    template_name = "schools/district_list.html"
    context_object_name = "districts"

    sort_fields = {"name": Lower("name")}
    default_sort_field = "name"

    def get_queryset(self):
        qs = super().get_queryset().annotate(school_count=Count("schools"))
        return self.apply_sorting(qs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["school_count"] = School.objects.filter(district__isnull=False).count()
        context["unspecified_school_count"] = School.objects.filter(
            district__isnull=True
        ).count()
        return context


class DistrictCreateView(
    LogFormSaveMixin, LoginRequiredMixin, PermissionRequiredMixin, CreateView
):
    model = SchoolDistrict
    form_class = SchoolDistrictForm
    template_name = "schools/district_form.html"
    permission_required = "programs.add_schooldistrict"

    def get_success_url(self):
        return reverse("school_district_list")


class DistrictUpdateView(
    LogFormSaveMixin, LoginRequiredMixin, PermissionRequiredMixin, UpdateView
):
    model = SchoolDistrict
    form_class = SchoolDistrictForm
    template_name = "schools/district_form.html"
    permission_required = "programs.change_schooldistrict"

    def get_success_url(self):
        return reverse("school_district_list")


class DistrictDeleteView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """Delete a district. Schools linked to it are detached (district set
    to null) but never deleted.
    """

    permission_required = "programs.delete_schooldistrict"
    template_name = "schools/district_confirm_delete.html"

    def get(self, request, *args, **kwargs):
        district = get_object_or_404(SchoolDistrict, pk=kwargs["pk"])
        return render(
            request,
            self.template_name,
            {"district": district, "schools": district.schools.all()},
        )

    def post(self, request, *args, **kwargs):
        district = get_object_or_404(SchoolDistrict, pk=kwargs["pk"])
        name = district.name
        district.delete()
        messages.success(
            request, f'Deleted district "{name}". Schools in it were unassigned.'
        )
        return redirect("school_district_list")
