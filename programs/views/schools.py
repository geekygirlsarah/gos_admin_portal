from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.db import transaction
from django.db.models import Case, CharField, Count, Prefetch, Q, Value, When
from django.db.models.functions import Lower
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.views.generic import CreateView, FormView, ListView, UpdateView, View

from organizations.mixins import OrganizationScopeMixin

from ..forms import SchoolDistrictForm, SchoolForm, SchoolMergeForm
from ..models import School, SchoolDistrict
from ..utils import get_safe_url
from .mixins import (
    LogFormSaveMixin,
    SortableListViewMixin,
    TenantLeadMentorPermissionRequiredMixin,
)


class SchoolListView(LoginRequiredMixin, SortableListViewMixin, ListView):
    model = School
    template_name = "schools/list.html"
    context_object_name = "schools"

    sort_fields = {
        "name": Lower("name"),
        "district": "_district_sort",
        "city": Lower("city"),
        "state": "state",
    }
    default_sort_field = "name"

    def get_queryset(self):
        organization = getattr(self.request, "organization", None)
        districts = SchoolDistrict.objects.for_organization(organization)
        district_sort = Lower("district__name")
        if organization is not None:
            district_sort = Case(
                When(district__organization=organization, then=district_sort),
                default=Value(None),
                output_field=CharField(),
            )
        qs = (
            super()
            .get_queryset()
            .for_organization(organization)
            .annotate(_district_sort=district_sort)
            .prefetch_related(Prefetch("district", queryset=districts))
        )
        return self.apply_sorting(qs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        organization = getattr(self.request, "organization", None)
        schools = School.objects.for_organization(organization)
        context["total_schools_count"] = schools.count()
        context["districts_count"] = SchoolDistrict.objects.for_organization(
            organization
        ).count()
        context["assigned_schools_count"] = schools.filter(
            district__isnull=False
        ).count()
        context["unassigned_schools_count"] = schools.filter(
            district__isnull=True
        ).count()
        return context


class SchoolCreateView(
    OrganizationScopeMixin,
    LogFormSaveMixin,
    LoginRequiredMixin,
    TenantLeadMentorPermissionRequiredMixin,
    CreateView,
):
    model = School
    form_class = SchoolForm
    template_name = "schools/form.html"
    permission_required = "programs.add_school"

    def form_valid(self, form):
        organization = self.get_organization()
        if organization is not None:
            form.instance.organization = organization
        return super().form_valid(form)

    def get_success_url(self):
        # After creating a School, return to the Schools listing
        return reverse("school_list")


class SchoolUpdateView(
    OrganizationScopeMixin,
    LogFormSaveMixin,
    LoginRequiredMixin,
    TenantLeadMentorPermissionRequiredMixin,
    UpdateView,
):
    model = School
    form_class = SchoolForm
    template_name = "schools/form.html"
    permission_required = "programs.change_school"

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .for_organization(getattr(self.request, "organization", None))
        )

    def get_success_url(self):
        next_url = self.request.GET.get("next")
        safe_url = get_safe_url(self.request, next_url)
        if safe_url:
            return safe_url
        return reverse("school_edit", args=[self.object.pk])


class SchoolMergeView(
    OrganizationScopeMixin,
    LoginRequiredMixin,
    TenantLeadMentorPermissionRequiredMixin,
    FormView,
):
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
        organization = self.get_organization()
        context["schools"] = list(
            School.objects.for_organization(organization).prefetch_related(
                Prefetch(
                    "district",
                    queryset=SchoolDistrict.objects.for_organization(organization),
                )
            )
        )
        return context

    def form_valid(self, form):
        from audit.events import AuditEvent
        from audit.service import log_event
        from programs.utils.merges import perform_school_merge

        keep_id = form.cleaned_data["keep"].pk
        source_id = form.cleaned_data["source"].pk
        with transaction.atomic():
            schools = {
                school.pk: school
                for school in School.objects.for_organization(self.get_organization())
                .filter(pk__in=[keep_id, source_id])
                .order_by("pk")
                .select_for_update()
            }
            keep = schools.get(keep_id)
            source = schools.get(source_id)
            if not keep or not source or keep.organization_id != source.organization_id:
                form.add_error(None, "The selected schools are no longer available.")
                return self.form_invalid(form)
            if source.district_id:
                district = SchoolDistrict.objects.select_for_update().get(
                    pk=source.district_id
                )
                if district.organization_id != source.organization_id:
                    form.add_error(None, "The source school has conflicting links.")
                    return self.form_invalid(form)
            list(source.students.select_for_update().values_list("pk", flat=True))
            if source.students.exclude(organization_id=source.organization_id).exists():
                form.add_error(None, "The source school has conflicting links.")
                return self.form_invalid(form)
            meta = perform_school_merge(keep, source)

        log_event(
            request=self.request,
            event=AuditEvent.RECORDS_MERGED,
            resource=keep,
            before={"source_name": meta["source_name"], "source_pk": meta["source_pk"]},
            after={"keep_name": meta["keep_name"], "keep_pk": meta["keep_pk"]},
            notes=(
                f'School "{meta["source_name"]}" (pk={meta["source_pk"]}) merged into '
                f'"{meta["keep_name"]}" (pk={meta["keep_pk"]}). All students were reassigned.'
            ),
        )

        messages.success(
            self.request,
            f'Merged "{meta["source_name"]}" into "{meta["keep_name"]}". '
            f"All students were moved to {meta['keep_name']}.",
        )
        return super().form_valid(form)


class DistrictListView(LoginRequiredMixin, SortableListViewMixin, ListView):
    model = SchoolDistrict
    template_name = "schools/district_list.html"
    context_object_name = "districts"

    sort_fields = {"name": Lower("name")}
    default_sort_field = "name"

    def get_queryset(self):
        organization = getattr(self.request, "organization", None)
        school_filter = Q()
        if organization is not None:
            school_filter = Q(schools__organization=organization)
        qs = (
            super()
            .get_queryset()
            .for_organization(organization)
            .annotate(school_count=Count("schools", filter=school_filter))
        )
        return self.apply_sorting(qs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        organization = getattr(self.request, "organization", None)
        schools = School.objects.for_organization(organization)
        context["districts_count"] = SchoolDistrict.objects.for_organization(
            organization
        ).count()
        context["school_count"] = schools.filter(district__isnull=False).count()
        context["unspecified_school_count"] = schools.filter(
            district__isnull=True
        ).count()
        return context


class DistrictCreateView(
    OrganizationScopeMixin,
    LogFormSaveMixin,
    LoginRequiredMixin,
    TenantLeadMentorPermissionRequiredMixin,
    CreateView,
):
    model = SchoolDistrict
    form_class = SchoolDistrictForm
    template_name = "schools/district_form.html"
    permission_required = "programs.add_schooldistrict"

    def form_valid(self, form):
        organization = self.get_organization()
        if organization is not None:
            form.instance.organization = organization
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("school_district_list")


class DistrictUpdateView(
    LogFormSaveMixin,
    LoginRequiredMixin,
    TenantLeadMentorPermissionRequiredMixin,
    UpdateView,
):
    model = SchoolDistrict
    form_class = SchoolDistrictForm
    template_name = "schools/district_form.html"
    permission_required = "programs.change_schooldistrict"

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .for_organization(getattr(self.request, "organization", None))
        )

    def get_success_url(self):
        return reverse("school_district_list")


class DistrictDeleteView(
    LoginRequiredMixin, TenantLeadMentorPermissionRequiredMixin, View
):
    """Delete a district. Schools linked to it are detached (district set
    to null) but never deleted.
    """

    permission_required = "programs.delete_schooldistrict"
    template_name = "schools/district_confirm_delete.html"

    def get(self, request, *args, **kwargs):
        organization = getattr(request, "organization", None)
        district = get_object_or_404(
            SchoolDistrict.objects.for_organization(organization), pk=kwargs["pk"]
        )
        return render(
            request,
            self.template_name,
            {
                "district": district,
                "schools": district.schools.for_organization(organization),
            },
        )

    def post(self, request, *args, **kwargs):
        from audit.service import log_deletion

        organization = getattr(request, "organization", None)
        with transaction.atomic():
            district = get_object_or_404(
                SchoolDistrict.objects.for_organization(
                    organization
                ).select_for_update(),
                pk=kwargs["pk"],
            )
            list(district.schools.select_for_update().values_list("pk", flat=True))
            if (
                organization is not None
                and district.schools.exclude(organization=organization).exists()
            ):
                messages.error(
                    request,
                    "This district cannot be deleted because of conflicting links.",
                )
                return redirect("school_district_list")
            name = district.name
            school_count = district.schools.for_organization(organization).count()
            with transaction.atomic():
                log_deletion(
                    resource=district,
                    request=request,
                    before={"name": name, "school_count": school_count},
                    notes=(
                        f"District '{name}' deleted; {school_count} school(s) left unassigned."
                    ),
                )
            district.delete()
        messages.success(
            request, f'Deleted district "{name}". Schools in it were unassigned.'
        )
        return redirect("school_district_list")
