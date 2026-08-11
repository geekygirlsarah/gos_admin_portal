import datetime
import logging

import cssutils
from django.contrib import messages
from django.contrib.auth.mixins import (
    LoginRequiredMixin,
    PermissionRequiredMixin,
    UserPassesTestMixin,
)
from django.db.models import Value
from django.db.models.functions import Coalesce, Lower, NullIf
from django.http import Http404, HttpResponseRedirect, QueryDict
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.html import strip_tags
from django.views.generic import (
    CreateView,
    DetailView,
    ListView,
    UpdateView,
    View,
)
from premailer import transform

from audit.events import AuditEvent
from audit.mixins import SensitiveDataViewMixin
from audit.service import log_event
from programs.constants import RELATIONSHIP_CHOICES

from ..forms import BackgroundChecksForm
from ..models import (
    Adult,
    AdultStudentRelationship,
    Enrollment,
    Fee,
    Payment,
    Program,
    RaceEthnicity,
    School,
    SlidingScale,
    SlidingScaleSettings,
    Student,
    SubTeam,
    TaxForm,
    Team,
)
from ..permission_views import (
    LeadMentorRequiredMixin,
    MentorOrLeadMentorRequiredMixin,
    PassUserToFormMixin,
    can_user_read,
    can_user_write,
    get_user_role,
)
from ..utils import (
    get_safe_url,
    redirect_back,
)

cssutils.log.setLevel(logging.WARNING)

logger = logging.getLogger("programs.email")
forms_logger = logging.getLogger("programs.forms")


class DynamicPermissionMixin(UserPassesTestMixin):
    section = None
    permission_type = "read"  # 'read' or 'write'

    def test_func(self):
        if not self.section:
            return True
        obj = getattr(self, "object", None)
        # Avoid calling get_object on CreateViews where 'pk' in URL refers to a parent
        if not obj and hasattr(self, "get_object") and not isinstance(self, CreateView):
            try:
                obj = self.get_object()
            except Http404:
                # If object doesn't exist, we treat it as a permission failure
                # so it redirects to dashboard with an error instead of 404ing
                return False
            except Exception:  # nosec B110
                pass
        if self.permission_type == "write":
            return can_user_write(self.request.user, self.section, obj)
        return can_user_read(self.request.user, self.section, obj)

    def handle_no_permission(self):
        model_name = "record"
        if hasattr(self, "model") and self.model:
            model_name = self.model._meta.verbose_name.lower()

        messages.error(
            self.request,
            f"You do not have permission to view that {model_name}, or it does not exist.",
        )
        return redirect("home")


class DynamicReadPermissionMixin(DynamicPermissionMixin):
    permission_type = "read"


class DynamicWritePermissionMixin(DynamicPermissionMixin):
    permission_type = "write"


class BackgroundChecksInlineMixin:
    """Add inline PA background-check editing to a student/adult update view.

    Editing is gated on ``can_user_write('background_checks', obj)`` which by
    default only grants access to Lead Mentors. Subclasses must set
    ``background_checks_kwarg`` to either ``"student"`` or ``"adult"``.
    """

    background_checks_kwarg = None

    def _bg_holder(self):
        holder_kwargs = {self.background_checks_kwarg: self.object}
        return holder_kwargs

    def _can_edit_bg(self):
        return can_user_write(self.request.user, "background_checks", self.object)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        if getattr(self, "object", None) and self.object.pk:
            bg_form = BackgroundChecksForm()
            bg_form.initial_from_holder(**self._bg_holder())
            ctx["bg_checks_form"] = bg_form
            ctx["can_edit_bg"] = self._can_edit_bg()
        return ctx

    def _save_background_checks(self):
        if not self.background_checks_kwarg:
            return
        if not self._can_edit_bg():
            return
        bg_form = BackgroundChecksForm(self.request.POST)
        bg_form.initial_from_holder(**self._bg_holder())
        if bg_form.is_valid():
            bg_form.save(**self._bg_holder())

    def form_valid(self, form):
        self._save_background_checks()
        return super().form_valid(form)


class StudentQuerysetRoleMixin:
    """Mixin for views that list Students (or querysets related to a
    Student) which need to be restricted based on the requesting user's
    role: Parents only see their own students, Students only see
    themselves, and other roles see the queryset unrestricted.
    """

    def filter_students_by_role(
        self, qs, adults_field="adults", student_field="pk", empty_queryset=None
    ):
        if empty_queryset is None:
            empty_queryset = Student.objects.none()

        role = get_user_role(self.request.user)
        if role == "Parent":
            try:
                adult = self.request.user.adult_profile
                qs = qs.filter(**{adults_field: adult})
            except (Adult.DoesNotExist, AttributeError):
                qs = empty_queryset
        elif role == "Student":
            try:
                student = self.request.user.student_profile
                value = student.pk if student_field == "pk" else student
                qs = qs.filter(**{student_field: value})
            except (Student.DoesNotExist, AttributeError):
                qs = empty_queryset
        return qs


class LogFormSaveMixin:
    """Mixin to log create/update actions and field changes for ModelForm-based CBVs.

    Logs at INFO level using logger name 'programs.forms'.
    """

    def _fmt_val(self, v):
        try:
            if v is None:
                return "∅"
            s = str(v)
            if len(s) > 200:
                s = s[:200] + "…"
            return s
        except Exception:
            return "<unrepr>"

    def form_valid(self, form):
        model = getattr(form._meta, "model", None)
        model_name = getattr(model, "__name__", form.__class__.__name__)
        user = getattr(getattr(self, "request", None), "user", None)
        user_repr = (
            f"{getattr(user, 'pk', 'anon')}:{getattr(user, 'username', 'anonymous')}"
            if getattr(user, "is_authenticated", False)
            else "anonymous"
        )
        is_create = not bool(getattr(form.instance, "pk", None))

        # Capture changes before saving (for updates)
        changes = []
        try:
            changed_fields = list(getattr(form, "changed_data", []) or [])
            if not is_create and changed_fields and model:
                # Reload from DB to ensure we have current values
                before = model.objects.get(pk=form.instance.pk)
                for f in changed_fields:
                    old = getattr(before, f, None)
                    new = form.cleaned_data.get(f, getattr(form.instance, f, None))
                    if old != new:
                        changes.append((f, old, new))
            elif is_create and changed_fields:
                for f in changed_fields:
                    new = form.cleaned_data.get(f, getattr(form.instance, f, None))
                    changes.append((f, None, new))
        except Exception:
            # Never fail the request due to logging
            changes = []
        except Exception:
            # Never fail the request due to logging
            changes = []

        response = super().form_valid(form)

        obj_id = getattr(
            getattr(self, "object", None), "pk", getattr(form.instance, "pk", None)
        )
        action = "create" if is_create else "update"
        if changes:
            # Map model/action to high-level AuditEvent
            event_map = {
                "Student": AuditEvent.CONTACT_INFO_UPDATED,
                "Adult": AuditEvent.CONTACT_INFO_UPDATED,
                "Enrollment": AuditEvent.ENROLLMENT_CHANGED,
                "AdultStudentRelationship": (
                    AuditEvent.GUARDIAN_ADDED
                    if is_create
                    else AuditEvent.CONTACT_INFO_UPDATED
                ),
            }
            event = event_map.get(str(model_name))
            if event:
                try:
                    before_dict = {
                        str(f): self._fmt_val(old) for f, old, new in changes
                    }
                    after_dict = {str(f): self._fmt_val(new) for f, old, new in changes}
                    log_event(
                        request=getattr(self, "request", None),
                        event=event,
                        resource=form.instance,
                        before=before_dict,
                        after=after_dict,
                        notes=f"{model_name} {action}ed via form save.",
                    )
                except Exception:  # nosec B110
                    # Never crash the main flow for audit logging
                    pass

            for f, old, new in changes:
                forms_logger.info(
                    "FormSave: %s[%s] %s by %s | field=%s | from=%s | to=%s",
                    model_name,
                    obj_id,
                    action,
                    user_repr,
                    f,
                    self._fmt_val(old),
                    self._fmt_val(new),
                )
        else:
            forms_logger.info(
                "FormSave: %s[%s] %s by %s | no field-level differences detected",
                model_name,
                obj_id,
                action,
                user_repr,
            )
        return response


class SortableListViewMixin:
    """Mixin for ListView to support sorting by columns."""

    sort_fields = {}  # Map of field names to actual queryset order_by values
    default_sort_field = None
    default_sort_dir = "asc"

    def get_sort_field(self):
        sort = self.request.GET.get("sort")
        if sort in self.sort_fields:
            return sort
        return self.default_sort_field

    def get_sort_dir(self):
        d = self.request.GET.get("dir", self.default_sort_dir)
        return "desc" if d == "desc" else "asc"

    def apply_sorting(self, queryset):
        sort = self.get_sort_field()
        if not sort:
            return queryset

        direction = self.get_sort_dir()
        order_by_value = self.sort_fields[sort]

        if isinstance(order_by_value, str):
            if direction == "desc":
                if order_by_value.startswith("-"):
                    order_by_value = order_by_value[1:]
                else:
                    order_by_value = f"-{order_by_value}"
            return queryset.order_by(order_by_value)
        elif isinstance(order_by_value, (list, tuple)):
            final_order = []
            for item in order_by_value:
                if isinstance(item, str):
                    if direction == "desc":
                        if item.startswith("-"):
                            final_order.append(item[1:])
                        else:
                            final_order.append(f"-{item}")
                    else:
                        final_order.append(item)
                else:
                    # Handle expressions like Lower('field')
                    if direction == "desc":
                        final_order.append(item.desc())
                    else:
                        final_order.append(item.asc())
            return queryset.order_by(*final_order)
        else:
            # Handle expressions like Lower('field')
            if direction == "desc":
                return queryset.order_by(order_by_value.desc())
            else:
                return queryset.order_by(order_by_value.asc())

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["current_sort"] = self.get_sort_field()
        ctx["current_dir"] = self.get_sort_dir()
        return ctx
