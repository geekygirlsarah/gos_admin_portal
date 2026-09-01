from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.db import models, transaction
from django.db.models import Value
from django.db.models.functions import Coalesce, Lower, NullIf
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.views.generic import CreateView, DetailView, FormView, ListView, UpdateView

from attendance.models import AttendanceEvent, AttendanceSession, RFIDCard
from audit.mixins import SensitiveDataViewMixin
from outreach.models import OutreachMentorSignup

from ..constants import RELATIONSHIP_CHOICES
from ..forms import AdultForm, ParentMergeForm
from ..models import (
    Adult,
    AdultStudentRelationship,
    BackgroundCheck,
    MentorAgreementAcceptance,
    MentorAgreementSubmission,
    Program,
    SlidingScale,
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
        "name": (
            Lower(
                Coalesce(NullIf("preferred_first_name", Value("")), "legal_first_name")
            ),
            Lower("last_name"),
        ),
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
        "name": (
            Lower(
                Coalesce(NullIf("preferred_first_name", Value("")), "legal_first_name")
            ),
            Lower("last_name"),
        ),
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
        "name": (
            Lower(
                Coalesce(NullIf("preferred_first_name", Value("")), "legal_first_name")
            ),
            Lower("last_name"),
        ),
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
        # Split the already-evaluated (and prefetched) list in Python.
        # Calling .filter() here would re-execute the whole list query and
        # its prefetches once per section.
        mentors = list(ctx["mentors"])
        ctx["active_mentors"] = [m for m in mentors if m.mentor_active]
        ctx["inactive_mentors"] = [m for m in mentors if not m.mentor_active]
        return ctx


class AlumniListView(LoginRequiredMixin, SortableListViewMixin, ListView):
    model = Adult
    template_name = "alumni/list.html"
    context_object_name = "alumni"

    sort_fields = {
        "name": (
            Lower(
                Coalesce(NullIf("preferred_first_name", Value("")), "legal_first_name")
            ),
            Lower("last_name"),
        ),
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


def _transfer_parent_related_records(keep, source):
    """Transfer or merge all related models referencing ``source`` onto ``keep``."""
    # 1. Background checks
    for bg in list(BackgroundCheck.objects.filter(adult=source)):
        existing = BackgroundCheck.objects.filter(
            adult=keep, check_type=bg.check_type
        ).first()
        if existing:
            updated = False
            if not existing.cleared and bg.cleared:
                existing.cleared = True
                updated = True
            if not existing.obtained_date and bg.obtained_date:
                existing.obtained_date = bg.obtained_date
                updated = True
            if updated:
                existing.save()
            bg.delete()
        else:
            bg.adult = keep
            bg.save(update_fields=["adult"])

    # 2. Mentor agreement acceptances & submissions
    for acc in list(MentorAgreementAcceptance.objects.filter(adult=source)):
        if not MentorAgreementAcceptance.objects.filter(
            adult=keep, agreement=acc.agreement
        ).exists():
            acc.adult = keep
            acc.save(update_fields=["adult"])
        else:
            acc.delete()

    for sub in list(MentorAgreementSubmission.objects.filter(adult=source)):
        if not MentorAgreementSubmission.objects.filter(
            adult=keep, agreement=sub.agreement
        ).exists():
            sub.adult = keep
            sub.save(update_fields=["adult"])
        else:
            sub.delete()

    # 3. Outreach mentor signups
    for signup in list(OutreachMentorSignup.objects.filter(adult=source)):
        if not OutreachMentorSignup.objects.filter(
            adult=keep, shift=signup.shift
        ).exists():
            signup.adult = keep
            signup.save(update_fields=["adult"])
        else:
            signup.delete()

    # 4. RFID cards
    for card in list(RFIDCard.objects.filter(adult=source)):
        if not RFIDCard.objects.filter(adult=keep, uid=card.uid).exists():
            card.adult = keep
            card.save(update_fields=["adult"])
        else:
            card.delete()

    # 5. Sliding scale applications
    SlidingScale.objects.filter(applied_by=source).update(applied_by=keep)

    # 6. Attendance records (protected FKs)
    AttendanceSession.objects.filter(adult=source).update(adult=keep)
    AttendanceEvent.objects.filter(adult=source).update(adult=keep)

    # 7. Sponsored Andrew IDs on Adults and Students
    Adult.objects.filter(andrew_id_sponsor=source).update(andrew_id_sponsor=keep)
    Student.objects.filter(andrew_id_sponsor=source).update(andrew_id_sponsor=keep)


def _carry_over_missing_parent_fields(keep, source):
    """Copy fields that only ``source`` has onto ``keep``.

    Returns True if ``keep`` was modified. Choice fields with model defaults
    ("cell" for phone_type, "PA" for state, "mentor" for role) look filled even
    when they were never actually chosen, so they are only treated as missing
    when the value they describe is missing too.
    """
    keep_had_phone = bool(keep.phone_number and str(keep.phone_number).strip())
    keep_had_address = bool(
        (keep.address and str(keep.address).strip())
        or (keep.city and str(keep.city).strip())
    )
    keep_had_mentor_role = bool(keep.is_mentor and keep.role and keep.role != "mentor")

    changed = False

    # 1. OneToOne student_record (Alumni profile link)
    if keep.student_record_id is None and source.student_record_id is not None:
        rec = source.student_record
        source.student_record = None
        source.save(update_fields=["student_record"])
        keep.student_record = rec
        changed = True

    # 2. Andrew ID sponsor FK
    if keep.andrew_id_sponsor_id is None and source.andrew_id_sponsor_id is not None:
        if source.andrew_id_sponsor_id == source.pk:
            keep.andrew_id_sponsor = keep
        else:
            keep.andrew_id_sponsor = source.andrew_id_sponsor
        changed = True

    # 3. Photo (ImageField)
    if not bool(keep.photo) and bool(source.photo):
        keep.photo = source.photo
        changed = True

    # 4. Defaulted choice fields
    if not keep_had_phone and source.phone_type and source.phone_number:
        keep.phone_type = source.phone_type
        changed = True
    if not keep_had_address and (source.address or source.city) and source.state:
        keep.state = source.state
        changed = True
    if (
        not keep_had_mentor_role
        and source.is_mentor
        and source.role
        and source.role != "mentor"
    ):
        keep.role = source.role
        changed = True

    # 5. Dynamic field iteration for all other fields on Adult
    handled_special_fields = {
        "id",
        "user",
        "is_parent",
        "is_mentor",
        "is_alumni",
        "student_record",
        "andrew_id_sponsor",
        "photo",
        "phone_type",
        "state",
        "role",
        "created_at",
        "updated_at",
    }

    for field in Adult._meta.fields:
        field_name = field.name
        if field_name in handled_special_fields:
            continue

        keep_val = getattr(keep, field_name)
        source_val = getattr(source, field_name)

        if isinstance(field, models.BooleanField):
            # If keep is False and source is True, promote to True
            if not keep_val and source_val:
                setattr(keep, field_name, True)
                changed = True
        else:
            # String, Date, Integer, Text, Email, etc.
            is_keep_empty = keep_val is None or (
                isinstance(keep_val, str) and not keep_val.strip()
            )
            is_source_filled = source_val is not None and (
                not isinstance(source_val, str) or bool(source_val.strip())
            )
            if is_keep_empty and is_source_filled:
                setattr(keep, field_name, source_val)
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
            .order_by("legal_first_name", "last_name")
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
            _transfer_parent_related_records(keep, source)
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
