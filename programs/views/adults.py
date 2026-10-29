from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.db.models import Value
from django.db.models.functions import Coalesce, Lower, NullIf
from django.shortcuts import get_object_or_404
from django.urls import reverse, reverse_lazy
from django.views.generic import CreateView, DetailView, FormView, ListView, UpdateView

from audit.mixins import SensitiveDataViewMixin

from ..constants import RELATIONSHIP_CHOICES
from ..forms import AdultForm, ParentMergeForm
from ..models import (
    Adult,
    AdultStudentRelationship,
    BackgroundCheckType,
    Program,
)
from ..permission_views import (
    LeadMentorRequiredMixin,
    PassUserToFormMixin,
    get_user_role,
)
from ..utils import (
    active_alumni,
    active_parents,
    get_safe_url,
)
from ..utils.merges import (
    _carry_over_missing_parent_fields,
    _merge_parent_role_flags,
    _transfer_parent_related_records,
    _transfer_parent_relationships,
    perform_parent_merge,
)
from .mixins import (
    BackgroundChecksInlineMixin,
    DynamicPermissionMixin,
    DynamicReadPermissionMixin,
    DynamicWritePermissionMixin,
    LogFormSaveMixin,
    SortableListViewMixin,
)


class AdultsListView(
    LoginRequiredMixin, DynamicReadPermissionMixin, SortableListViewMixin, ListView
):
    model = Adult
    template_name = "adults/list.html"
    context_object_name = "adults"
    section = "adult_info"

    sort_fields = {
        "name": (Lower("sort_first"), Lower("last_name")),
        "email": Lower("personal_email"),
        "phone": "phone_number",
        "login_enabled": "login_enabled",
    }
    default_sort_field = "name"

    def apply_sorting(self, qs):
        # Always prepend -login_enabled to sorting if not already sorting by login_enabled
        sort = self.get_sort_field()
        qs = super().apply_sorting(qs)
        if sort != "login_enabled":
            qs = qs.order_by("-login_enabled", *qs.query.order_by)
        return qs

    def get_queryset(self):

        qs = (
            Adult.objects.for_organization(getattr(self.request, "organization", None))
            .annotate(
                sort_first=Coalesce(
                    NullIf("preferred_first_name", Value("")), "legal_first_name"
                )
            )
            .prefetch_related(
                "students",
                "email_delivery_events",
                "background_checks__check_type",
            )
        )
        program_id = self.kwargs.get("program_id")
        if program_id:
            qs = qs.filter(students__enrollment__program_id=program_id).distinct()

        role = get_user_role(self.request.user)
        if role == "Parent":
            try:
                adult = self.request.user.adult_profile
                qs = qs.filter(pk=adult.pk)
            except (Adult.DoesNotExist, AttributeError):
                qs = Adult.objects.none()
        elif role == "Mentor":
            qs = qs.filter(
                is_parent=True, students__enrollment__program__active=True
            ).distinct()
        return self.apply_sorting(qs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        ctx["role"] = get_user_role(self.request.user)
        program_id = self.kwargs.get("program_id")
        if program_id:
            ctx["program"] = get_object_or_404(Program, pk=program_id)

        adults = list(ctx.get("adults") or ctx.get("object_list") or [])
        ctx["total_adults_count"] = len(adults)
        ctx["parents_count"] = sum(1 for a in adults if a.is_parent)
        ctx["mentors_count"] = sum(1 for a in adults if a.is_mentor)
        ctx["alumni_count"] = sum(1 for a in adults if a.is_alumni)
        return ctx


class ParentListView(LoginRequiredMixin, SortableListViewMixin, ListView):
    model = Adult
    template_name = "parents/list.html"
    context_object_name = "parents"

    sort_fields = {
        "name": (Lower("sort_first"), Lower("last_name")),
        "email": Lower("personal_email"),
        "phone": "phone_number",
    }
    default_sort_field = "name"

    def get_queryset(self):
        qs = (
            active_parents(getattr(self.request, "organization", None))
            .annotate(
                sort_first=Coalesce(
                    NullIf("preferred_first_name", Value("")), "legal_first_name"
                )
            )
            .prefetch_related(
                "students__school",
                "students__enrollment_set__program",
                "adultstudentrelationship_set",
            )
        )
        program_id = self.kwargs.get("program_id")
        if program_id:
            qs = qs.filter(students__enrollment__program_id=program_id).distinct()

        role = get_user_role(self.request.user)
        if role == "Parent":
            try:
                adult = self.request.user.adult_profile
                student_ids = adult.students.values_list("id", flat=True)
                qs = qs.filter(students__id__in=student_ids).distinct()
            except:
                qs = Adult.objects.none()
        elif role == "Student":
            try:
                student = self.request.user.student_profile
                qs = qs.filter(students=student).distinct()
            except:
                qs = Adult.objects.none()

        return self.apply_sorting(qs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        program_id = self.kwargs.get("program_id")
        if program_id:
            ctx["program"] = get_object_or_404(Program, pk=program_id)
        parents = list(ctx.get("parents") or ctx.get("object_list") or [])
        ctx["total_parents_count"] = len(parents)
        ctx["email_updates_count"] = sum(1 for p in parents if p.email_updates)
        return ctx


class MentorListView(LoginRequiredMixin, SortableListViewMixin, ListView):
    model = Adult
    template_name = "mentors/list.html"
    context_object_name = "mentors"

    sort_fields = {
        "name": (Lower("sort_first"), Lower("last_name")),
        "role": "role",
        "mentor_active": "mentor_active",
    }
    default_sort_field = "name"

    def apply_sorting(self, qs):
        # Always prepend -mentor_active to sorting if not already sorting by mentor_active
        sort = self.get_sort_field()
        qs = super().apply_sorting(qs)
        if sort != "mentor_active":
            qs = qs.order_by("-mentor_active", *qs.query.order_by)
        return qs

    def get_queryset(self):
        qs = (
            Adult.objects.for_organization(getattr(self.request, "organization", None))
            .filter(is_mentor=True)
            .annotate(
                sort_first=Coalesce(
                    NullIf("preferred_first_name", Value("")), "legal_first_name"
                )
            )
            .prefetch_related(
                "students__enrollment_set__program",
                "background_checks__check_type",
            )
        )
        program_id = self.kwargs.get("program_id")
        if program_id:
            qs = qs.filter(students__enrollment__program_id=program_id).distinct()
        return self.apply_sorting(qs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        program_id = self.kwargs.get("program_id")
        if program_id:
            ctx["program"] = get_object_or_404(Program, pk=program_id)
        # Split the already-evaluated (and prefetched) list in Python.
        # Calling .filter() here would re-execute the whole list query and
        # its prefetches once per section.
        mentors = list(ctx.get("mentors") or ctx.get("object_list") or [])
        org = getattr(self.request, "organization", None)
        active_types = list(
            BackgroundCheckType.objects.for_organization(org).filter(is_active=True)
        )
        ctx["active_mentors"] = [m for m in mentors if m.mentor_active]
        ctx["inactive_mentors"] = [m for m in mentors if not m.mentor_active]
        ctx["active_mentors_count"] = len(ctx["active_mentors"])
        ctx["inactive_mentors_count"] = len(ctx["inactive_mentors"])
        ctx["bg_checks_needed_count"] = sum(
            1 for m in mentors if m.needs_background_check(active_types=active_types)
        )
        return ctx


class AlumniListView(LoginRequiredMixin, SortableListViewMixin, ListView):
    model = Adult
    template_name = "alumni/list.html"
    context_object_name = "alumni"

    sort_fields = {
        "name": (Lower("sort_first"), Lower("last_name")),
        "email": Lower("personal_email"),
        "phone": "phone_number",
        "college": Lower("college"),
        "employer": Lower("employer"),
        "ok_to_contact": "ok_to_contact",
    }
    default_sort_field = "name"

    def get_queryset(self):
        qs = (
            active_alumni(getattr(self.request, "organization", None))
            .annotate(
                sort_first=Coalesce(
                    NullIf("preferred_first_name", Value("")), "legal_first_name"
                )
            )
            .prefetch_related("students__enrollment_set__program")
        )
        program_id = self.kwargs.get("program_id")
        if program_id:
            qs = qs.filter(students__enrollment__program_id=program_id).distinct()
        return self.apply_sorting(qs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        program_id = self.kwargs.get("program_id")
        if program_id:
            ctx["program"] = get_object_or_404(Program, pk=program_id)
        alumni = list(ctx.get("alumni") or ctx.get("object_list") or [])
        ctx["total_alumni_count"] = len(alumni)
        ctx["ok_to_contact_count"] = sum(1 for a in alumni if a.ok_to_contact)
        return ctx


class AdultDetailView(
    DynamicPermissionMixin, SensitiveDataViewMixin, LoginRequiredMixin, DetailView
):
    model = Adult
    template_name = "adults/detail.html"
    context_object_name = "adult"
    section = "adult_info"

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .prefetch_related(
                "students",
                "adultstudentrelationship_set",
                "background_checks__check_type",
                "email_delivery_events",
            )
        )

    def get_object(self, queryset=None):
        # Cache the fetched record: DynamicPermissionMixin's test_func calls
        # get_object() before DetailView.get() does it again.
        if getattr(self, "object", None) is None:
            self.object = super().get_object(queryset)
        return self.object

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        ctx["role"] = get_user_role(self.request.user)
        return ctx


class ParentCreateView(
    PassUserToFormMixin,
    LogFormSaveMixin,
    LoginRequiredMixin,
    PermissionRequiredMixin,
    CreateView,
):
    model = Adult
    form_class = AdultForm
    template_name = "adults/form.html"
    permission_required = "programs.add_adult"

    def get_initial(self):
        initial = super().get_initial()
        return initial

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["back_url"] = self.request.META.get("HTTP_REFERER", "/")
        return ctx

    def form_valid(self, form):
        # Ensure adults created via this view are flagged as parents if the field was hidden
        if "is_parent" not in form.fields:
            form.instance.is_parent = True
        response = super().form_valid(form)
        messages.success(self.request, "Parent added successfully.")
        return response

    def get_success_url(self):
        # After creating a Parent, return to the Parents listing or the 'next' URL
        next_url = self.request.GET.get("next")
        safe_url = get_safe_url(self.request, next_url)
        if safe_url:
            return safe_url
        return reverse("parent_list")


class ParentUpdateView(
    PassUserToFormMixin,
    SensitiveDataViewMixin,
    LogFormSaveMixin,
    LoginRequiredMixin,
    DynamicWritePermissionMixin,
    BackgroundChecksInlineMixin,
    UpdateView,
):
    model = Adult
    form_class = AdultForm
    template_name = "adults/form.html"
    permission_required = "programs.change_adult"
    section = "adult_info"
    background_checks_kwarg = "adult"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["back_url"] = self.request.META.get("HTTP_REFERER", "/")
        return ctx

    def get_success_url(self):
        next_url = self.request.GET.get("next")
        safe_url = get_safe_url(self.request, next_url)
        if safe_url:
            return safe_url
        return reverse("parent_list")


class AdultCreateView(
    PassUserToFormMixin,
    LogFormSaveMixin,
    LoginRequiredMixin,
    PermissionRequiredMixin,
    CreateView,
):
    model = Adult
    form_class = AdultForm
    template_name = "adults/form.html"
    permission_required = "programs.add_adult"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["back_url"] = self.request.META.get("HTTP_REFERER", "/")
        return ctx

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Adult added successfully.")
        return response

    def get_success_url(self):
        next_url = self.request.GET.get("next")
        safe_url = get_safe_url(self.request, next_url)
        if safe_url:
            return safe_url
        return reverse("adult_list")


class AdultUpdateView(
    PassUserToFormMixin,
    SensitiveDataViewMixin,
    LogFormSaveMixin,
    LoginRequiredMixin,
    DynamicWritePermissionMixin,
    BackgroundChecksInlineMixin,
    UpdateView,
):
    model = Adult
    form_class = AdultForm
    template_name = "adults/form.html"
    permission_required = "programs.change_adult"
    section = "adult_info"
    background_checks_kwarg = "adult"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["back_url"] = self.request.META.get("HTTP_REFERER", "/")
        ctx["RELATIONSHIP_CHOICES"] = RELATIONSHIP_CHOICES
        adult = self.object
        if adult and adult.pk:
            rels = {
                r.student_id: (r.relationship_to_student, r.specific_relationship or "")
                for r in adult.adultstudentrelationship_set.select_related(
                    "student"
                ).all()
            }
            ctx["linked_students"] = [
                {
                    "student": s,
                    "rel": rels.get(s.pk, ("", ""))[0],
                    "specific_rel": rels.get(s.pk, ("", ""))[1],
                }
                for s in adult.students.select_related("school").order_by(
                    "last_name", "preferred_first_name"
                )
            ]
        return ctx

    def form_valid(self, form):
        response = super().form_valid(form)
        rel_map = {
            k[len("student_rel_") :]: v  # noqa: E203
            for k, v in self.request.POST.items()
            if k.startswith("student_rel_")
        }
        specific_map = {
            k[len("student_specific_rel_") :]: v  # noqa: E203
            for k, v in self.request.POST.items()
            if k.startswith("student_specific_rel_")
        }
        valid_keys = set(k for k, _ in RELATIONSHIP_CHOICES)
        for sid_str, rel in rel_map.items():
            try:
                sid = int(sid_str)
            except (TypeError, ValueError):
                continue
            defaults = {}
            if rel in valid_keys:
                defaults["relationship_to_student"] = rel
            specific = specific_map.get(sid_str, "")
            if specific:
                defaults["specific_relationship"] = specific
            if defaults:
                AdultStudentRelationship.objects.update_or_create(
                    adult=self.object,
                    student_id=sid,
                    defaults=defaults,
                )
        messages.success(self.request, "Adult record saved successfully.")
        return response

    def get_success_url(self):
        nxt = self.request.GET.get("next")
        safe_url = get_safe_url(self.request, nxt)
        if safe_url:
            return safe_url
        return reverse("adult_list")


class MentorCreateView(
    PassUserToFormMixin,
    LogFormSaveMixin,
    LoginRequiredMixin,
    PermissionRequiredMixin,
    CreateView,
):
    model = Adult
    form_class = AdultForm
    template_name = "adults/form.html"
    permission_required = "programs.add_adult"

    def get_initial(self):
        ini = super().get_initial()
        return ini

    def form_valid(self, form):
        # Ensure adults created via this view are flagged as mentors if the field was hidden
        if "is_mentor" not in form.fields:
            form.instance.is_mentor = True
        return super().form_valid(form)

    def get_success_url(self):
        next_url = self.request.GET.get("next")
        safe_url = get_safe_url(self.request, next_url)
        if safe_url:
            return safe_url
        return reverse("mentor_list")


class MentorUpdateView(
    PassUserToFormMixin,
    SensitiveDataViewMixin,
    LogFormSaveMixin,
    LoginRequiredMixin,
    DynamicWritePermissionMixin,
    BackgroundChecksInlineMixin,
    UpdateView,
):
    model = Adult
    form_class = AdultForm
    template_name = "adults/form.html"
    permission_required = "programs.change_adult"
    section = "adult_info"
    background_checks_kwarg = "adult"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["back_url"] = self.request.META.get("HTTP_REFERER", "/")
        ctx["RELATIONSHIP_CHOICES"] = RELATIONSHIP_CHOICES
        adult = self.object
        if adult and adult.pk:
            rels = {
                r.student_id: (r.relationship_to_student, r.specific_relationship or "")
                for r in adult.adultstudentrelationship_set.select_related(
                    "student"
                ).all()
            }
            ctx["linked_students"] = [
                {
                    "student": s,
                    "rel": rels.get(s.pk, ("", ""))[0],
                    "specific_rel": rels.get(s.pk, ("", ""))[1],
                }
                for s in adult.students.select_related("school").order_by(
                    "last_name", "preferred_first_name"
                )
            ]
        return ctx

    def form_valid(self, form):
        response = super().form_valid(form)
        rel_map = {
            k[len("student_rel_") :]: v  # noqa: E203
            for k, v in self.request.POST.items()
            if k.startswith("student_rel_")
        }
        specific_map = {
            k[len("student_specific_rel_") :]: v  # noqa: E203
            for k, v in self.request.POST.items()
            if k.startswith("student_specific_rel_")
        }
        valid_keys = set(k for k, _ in RELATIONSHIP_CHOICES)
        for sid_str, rel in rel_map.items():
            try:
                sid = int(sid_str)
            except (TypeError, ValueError):
                continue
            defaults = {}
            if rel in valid_keys:
                defaults["relationship_to_student"] = rel
            specific = specific_map.get(sid_str, "")
            if specific:
                defaults["specific_relationship"] = specific
            if defaults:
                AdultStudentRelationship.objects.update_or_create(
                    adult=self.object,
                    student_id=sid,
                    defaults=defaults,
                )
        messages.success(self.request, "Adult record saved successfully.")
        return response

    def get_success_url(self):
        next_url = self.request.GET.get("next")
        safe_url = get_safe_url(self.request, next_url)
        if safe_url:
            return safe_url
        return reverse("mentor_list")


class ParentMergeView(LeadMentorRequiredMixin, FormView):
    """Merge two parent/adult records that represent the same person.

    The ``keep`` parent survives. Missing fields are filled from the ``source``
    parent, all student relationships are transferred, and the source is deleted.
    """

    form_class = ParentMergeForm
    template_name = "parents/merge.html"
    success_url = reverse_lazy("parent_list")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["parents"] = list(
            active_parents()
            .annotate(
                sort_first=Coalesce(
                    NullIf("preferred_first_name", Value("")), "legal_first_name"
                )
            )
            .order_by(Lower("sort_first"), Lower("last_name"))
            .prefetch_related(
                "students__school",
                "students__enrollment_set__program",
                "adultstudentrelationship_set",
            )
        )
        return context

    def form_valid(self, form):
        keep = form.cleaned_data["keep"]
        source = form.cleaned_data["source"]

        meta = perform_parent_merge(keep, source)

        from audit.events import AuditEvent
        from audit.service import log_event

        log_event(
            request=self.request,
            event=AuditEvent.RECORDS_MERGED,
            resource=keep,
            notes=(
                f'Parent "{meta["source_name"]}" (pk={meta["source_pk"]}) merged into '
                f'"{meta["keep_name"]}" (pk={meta["keep_pk"]}). All student relationships '
                f"were transferred."
            ),
        )

        messages.success(
            self.request,
            f'Merged "{meta["source_name"]}" into "{meta["keep_name"]}". '
            f"All student relationships were transferred.",
        )
        return super().form_valid(form)
