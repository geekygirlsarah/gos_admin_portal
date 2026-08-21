from django.contrib import messages
from django.contrib.auth.mixins import (
    LoginRequiredMixin,
    PermissionRequiredMixin,
)
from django.db import transaction
from django.db.models.functions import Lower
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.views.generic import (
    CreateView,
    DetailView,
    FormView,
    ListView,
    UpdateView,
)

from audit.mixins import SensitiveDataViewMixin

from ..constants import RELATIONSHIP_CHOICES
from ..forms import AdultForm, ParentMergeForm
from ..models import (
    Adult,
    AdultStudentRelationship,
    Program,
    Student,
)
from ..permission_views import (
    LeadMentorRequiredMixin,
    PassUserToFormMixin,
    can_user_read,
    get_user_role,
)
from ..utils import (
    active_adults,
    active_alumni,
    active_mentors,
    active_parents,
    get_safe_url,
    redirect_back,
)
from .mixins import (
    BackgroundChecksInlineMixin,
    DynamicPermissionMixin,
    DynamicReadPermissionMixin,
    DynamicWritePermissionMixin,
    LogFormSaveMixin,
    SortableListViewMixin,
    forms_logger,
)


class AdultsListView(
    LoginRequiredMixin, DynamicReadPermissionMixin, SortableListViewMixin, ListView
):
    model = Adult
    template_name = "adults/list.html"
    context_object_name = "adults"
    section = "adult_info"

    sort_fields = {
        "name": (Lower("first_name"), Lower("last_name")),
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

        qs = Adult.objects.all().prefetch_related("students")
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
        return ctx


class ParentListView(LoginRequiredMixin, SortableListViewMixin, ListView):
    model = Adult
    template_name = "parents/list.html"
    context_object_name = "parents"

    sort_fields = {
        "name": (Lower("first_name"), Lower("last_name")),
        "email": Lower("personal_email"),
        "phone": "phone_number",
    }
    default_sort_field = "name"

    def get_queryset(self):
        qs = active_parents().prefetch_related(
            "students__school",
            "students__enrollment_set__program",
            "adultstudentrelationship_set",
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
        return ctx


class MentorListView(LoginRequiredMixin, SortableListViewMixin, ListView):
    model = Adult
    template_name = "mentors/list.html"
    context_object_name = "mentors"

    sort_fields = {
        "name": (Lower("first_name"), Lower("last_name")),
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
        qs = Adult.objects.filter(is_mentor=True).prefetch_related(
            "students__enrollment_set__program",
            "background_checks",
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
        all_mentors = ctx["mentors"]
        ctx["active_mentors"] = all_mentors.filter(mentor_active=True)
        ctx["inactive_mentors"] = all_mentors.filter(mentor_active=False)
        return ctx


class AlumniListView(LoginRequiredMixin, SortableListViewMixin, ListView):
    model = Adult
    template_name = "alumni/list.html"
    context_object_name = "alumni"

    sort_fields = {
        "name": (Lower("first_name"), Lower("last_name")),
        "email": Lower("personal_email"),
        "phone": "phone_number",
        "college": Lower("college"),
        "employer": Lower("employer"),
        "ok_to_contact": "ok_to_contact",
    }
    default_sort_field = "name"

    def get_queryset(self):
        qs = active_alumni().prefetch_related("students__enrollment_set__program")
        program_id = self.kwargs.get("program_id")
        if program_id:
            qs = qs.filter(students__enrollment__program_id=program_id).distinct()
        return self.apply_sorting(qs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        program_id = self.kwargs.get("program_id")
        if program_id:
            ctx["program"] = get_object_or_404(Program, pk=program_id)
        return ctx


class AdultDetailView(
    DynamicPermissionMixin, SensitiveDataViewMixin, LoginRequiredMixin, DetailView
):
    model = Adult
    template_name = "adults/detail.html"
    context_object_name = "adult"
    section = "adult_info"

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

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["back_url"] = self.request.META.get("HTTP_REFERER", "/")
        return ctx

    def form_valid(self, form):
        # Ensure adults created via this view are flagged as parents
        obj = form.save(commit=False)
        obj.is_parent = True
        obj.save()
        # Save many-to-many after the object exists
        form.save_m2m()
        # Logging for creation with changed fields
        user = getattr(self.request, "user", None)
        user_repr = (
            f"{getattr(user, 'pk', 'anon')}:{getattr(user, 'username', 'anonymous')}"
            if getattr(user, "is_authenticated", False)
            else "anonymous"
        )
        for f in getattr(form, "changed_data", []) or []:
            new = form.cleaned_data.get(f, getattr(obj, f, None))
            forms_logger.info(
                "FormSave: %s[%s] %s by %s | field=%s | from=%s | to=%s",
                "Adult",
                obj.pk,
                "create",
                user_repr,
                f,
                self._fmt_val(None),
                self._fmt_val(new),
            )
        messages.success(self.request, "Parent added successfully.")
        return redirect("parent_list")

    def get_success_url(self):
        # After creating a Parent, return to the Parents listing
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
        return reverse("parent_edit", args=[self.object.pk])


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

    def get_success_url(self):
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
                    "last_name", "first_name"
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
        ini["is_mentor"] = True
        return ini

    def form_valid(self, form):
        form.instance.is_mentor = True
        return super().form_valid(form)

    def get_success_url(self):
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
                    "last_name", "first_name"
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
        return reverse("mentor_edit", args=[self.object.pk])


def _transfer_parent_relationships(keep, source):
    """Move all of ``source``'s student relationships onto ``keep``."""
    for rel in list(AdultStudentRelationship.objects.filter(adult=source)):
        existing = AdultStudentRelationship.objects.filter(
            adult=keep, student=rel.student
        ).first()
        if existing:
            if not existing.specific_relationship and rel.specific_relationship:
                existing.specific_relationship = rel.specific_relationship
                existing.save(update_fields=["specific_relationship"])
            Student.objects.filter(primary_contact_relationship=rel).update(
                primary_contact_relationship=existing
            )
            Student.objects.filter(secondary_contact_relationship=rel).update(
                secondary_contact_relationship=existing
            )
            rel.delete()
        else:
            rel.adult = keep
            rel.save(update_fields=["adult"])


def _carry_over_missing_parent_fields(keep, source):
    """Copy fields that only ``source`` has onto ``keep``.

    Returns True if ``keep`` was modified. Choice fields with model defaults
    ("cell" for phone_type, "PA" for state) look filled even when they were
    never actually chosen, so they are only treated as missing when the value
    they describe (phone number / address) is missing too.
    """
    keep_had_phone = bool(keep.phone_number)
    keep_had_address = bool(keep.address or keep.city)

    carryover_fields = [
        "personal_email",
        "phone_number",
        "address",
        "city",
        "zip_code",
        "pronouns",
        "can_receive_texts",
        "preferred_first_name",
        "emergency_contact_name",
        "emergency_contact_phone",
    ]
    changed = False
    for field in carryover_fields:
        keep_val = getattr(keep, field)
        source_val = getattr(source, field)
        if not keep_val and source_val:
            setattr(keep, field, source_val)
            changed = True

    if not keep_had_phone and source.phone_type:
        keep.phone_type = source.phone_type
        changed = True
    if not keep_had_address and source.address and source.state:
        keep.state = source.state
        changed = True

    return changed


def _merge_parent_role_flags(keep, source):
    """OR together the role flags. Returns True if ``keep`` was modified."""
    changed = False
    for flag in ("is_parent", "is_mentor", "is_alumni"):
        if getattr(source, flag) and not getattr(keep, flag):
            setattr(keep, flag, True)
            changed = True
    return changed


def _transfer_parent_user_account(keep, source):
    """Move ``source``'s linked user account to ``keep`` if it has none."""
    if keep.user_id or not source.user_id:
        return False
    source_user = source.user
    source.user = None
    source.save(update_fields=["user"])
    keep.user = source_user
    return True


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
            .order_by("first_name", "last_name")
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

        with transaction.atomic():
            _transfer_parent_relationships(keep, source)
            changed = _carry_over_missing_parent_fields(keep, source)
            changed = _merge_parent_role_flags(keep, source) or changed
            changed = _transfer_parent_user_account(keep, source) or changed
            if changed:
                keep.save()

            source.delete()

        from audit.events import AuditEvent
        from audit.service import log_event

        log_event(
            request=self.request,
            event=AuditEvent.RECORDS_MERGED,
            resource=keep,
            notes=(
                f'Parent "{source.full_name}" (pk={source.pk}) merged into '
                f'"{keep.full_name}" (pk={keep.pk}). All student relationships '
                f"were transferred."
            ),
        )

        messages.success(
            self.request,
            f'Merged "{source.full_name}" into "{keep.full_name}". '
            f"All student relationships were transferred.",
        )
        return super().form_valid(form)
