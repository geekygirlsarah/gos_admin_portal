from django.contrib import messages
from django.contrib.auth.mixins import (
    LoginRequiredMixin,
    PermissionRequiredMixin,
)
from django.db.models.functions import Lower
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.generic import (
    CreateView,
    DetailView,
    ListView,
    UpdateView,
)

from audit.mixins import SensitiveDataViewMixin

from ..forms import AdultForm
from ..models import (
    Adult,
    Program,
)
from ..permission_views import (
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
        "active": "active",
    }
    default_sort_field = "name"

    def apply_sorting(self, qs):
        # Always prepend -active to sorting if not already sorting by active
        sort = self.get_sort_field()
        qs = super().apply_sorting(qs)
        if sort != "active":
            qs = qs.order_by("-active", *qs.query.order_by)
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
            "students__school", "students__enrollment_set__program"
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
        "active": "active",
    }
    default_sort_field = "name"

    def apply_sorting(self, qs):
        # Always prepend -active to sorting if not already sorting by active
        sort = self.get_sort_field()
        qs = super().apply_sorting(qs)
        if sort != "active":
            qs = qs.order_by("-active", *qs.query.order_by)
        return qs

    def get_queryset(self):
        qs = Adult.objects.filter(is_mentor=True).prefetch_related(
            "students__enrollment_set__program"
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
        return ctx

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
        return ctx

    def get_success_url(self):
        next_url = self.request.GET.get("next")
        safe_url = get_safe_url(self.request, next_url)
        if safe_url:
            return safe_url
        return reverse("mentor_edit", args=[self.object.pk])
