from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    ListView,
    UpdateView,
)

from audit.events import AuditEvent
from audit.service import log_event
from programs.models import Student
from programs.permission_views import (
    LeadMentorRequiredMixin,
    can_user_read,
    can_user_write,
    get_user_role,
)

from .forms import BadgeForm
from .models import Badge, StudentBadge


class BadgeAwardPermissionMixin:
    """Allows awarding only if user has badge_award write (LeadMentor always)."""

    def dispatch(self, request, *args, **kwargs):
        if not can_user_write(request.user, "badge_award"):
            from django.contrib import messages as _messages
            from django.shortcuts import redirect as _redirect

            _messages.error(request, "You do not have permission to award badges.")
            return _redirect("badges:list")
        return super().dispatch(request, *args, **kwargs)


class BadgeManagePermissionMixin:
    """Allows create/edit/delete only if user has badge_manage write."""

    def dispatch(self, request, *args, **kwargs):
        if not can_user_write(request.user, "badge_manage"):
            from django.contrib import messages as _messages
            from django.shortcuts import redirect as _redirect

            _messages.error(request, "You do not have permission to manage badges.")
            return _redirect("badges:list")
        return super().dispatch(request, *args, **kwargs)


class BadgeListView(LoginRequiredMixin, View):
    def get(self, request):
        # If accessed with ?program=X, gate on that program's feature
        program_id = request.GET.get("program")
        if program_id:
            from programs.models import Program

            try:
                program = Program.objects.get(pk=int(program_id))
            except (Program.DoesNotExist, ValueError, TypeError):
                raise Http404("Program not found.")
            if not program.has_feature("badges"):
                raise Http404("Badges are not enabled for this program.")

        badges = Badge.objects.prefetch_related("prerequisites").all()
        student = None
        earned_ids = set()
        student_profile = getattr(request.user, "student_profile", None)
        if student_profile:
            student = student_profile
            earned_ids = set(
                StudentBadge.objects.filter(student=student).values_list(
                    "badge_id", flat=True
                )
            )
        student_id = request.GET.get("student")
        if student_id:
            try:
                s = Student.objects.get(pk=student_id)
            except (Student.DoesNotExist, ValueError, TypeError):
                s = None
            if s:
                from programs.permission_views import can_user_read

                if can_user_read(request.user, "badges", s):
                    student = s
                    earned_ids = set(
                        StudentBadge.objects.filter(student=s).values_list(
                            "badge_id", flat=True
                        )
                    )
        role = get_user_role(request.user)
        show_how = role in ("LeadMentor", "Mentor")
        is_lead = role == "LeadMentor"
        can_award = can_user_write(request.user, "badge_award")
        can_manage = can_user_write(request.user, "badge_manage")
        from .models import BadgeCategory

        # Check if the active student is enrolled in at least one badge-enabled program
        enrolled_in_badge_program = False
        if student:
            from programs.models import Enrollment

            enrolled_in_badge_program = Enrollment.objects.filter(
                student=student,
                active=True,
                program__features__key="badges",
            ).exists()

        return render(
            request,
            "badges/badge_list.html",
            {
                "badges": badges,
                "earned_ids": earned_ids,
                "student": student,
                "show_how": show_how,
                "awards": (
                    StudentBadge.objects.filter(student=student).select_related(
                        "badge", "awarded_by"
                    )
                    if student
                    else []
                ),
                "is_lead_mentor": is_lead,
                "can_award": can_award,
                "can_manage": can_manage,
                "enrolled_in_badge_program": enrolled_in_badge_program,
                "categories": BadgeCategory.choices,
            },
        )


class BadgeDetailView(LoginRequiredMixin, DetailView):
    model = Badge
    template_name = "badges/badge_detail.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        role = get_user_role(self.request.user)
        ctx["show_how"] = role in ("LeadMentor", "Mentor")
        ctx["is_lead_mentor"] = role == "LeadMentor"
        ctx["can_award"] = can_user_write(self.request.user, "badge_award")
        ctx["can_manage"] = can_user_write(self.request.user, "badge_manage")
        ctx["awards"] = StudentBadge.objects.filter(badge=self.object).select_related(
            "student", "awarded_by", "awarded_by__adult_profile"
        )
        # students list for award dropdown - if navigating within a program, only show that program's students, sorted
        import re

        from programs.models import Student as S

        m = re.match(r"^/programs/(\d+)/", self.request.path)
        # badges are global, but when accessed with ?program=ID or via current_program context, filter to that program
        program_id = self.request.GET.get("program") or (m.group(1) if m else None)

        # Gate on feature when a program context is present
        if program_id:
            from programs.models import Program

            try:
                program = Program.objects.get(pk=int(program_id))
            except (Program.DoesNotExist, ValueError, TypeError):
                raise Http404("Program not found.")
            if not program.has_feature("badges"):
                raise Http404("Badges are not enabled for this program.")

        # also check context processor's current_program via query param fallback
        qs = S.objects.all()
        if program_id:
            try:
                qs = qs.filter(
                    enrollment__program_id=int(program_id), enrollment__active=True
                ).distinct()
            except (ValueError, TypeError):
                pass
        else:
            # if user is viewing badges from a program page, referrer may contain program
            ref = self.request.META.get("HTTP_REFERER", "")
            mm = re.search(r"/programs/(\d+)/", ref)
            if mm:
                qs = qs.filter(
                    enrollment__program_id=int(mm.group(1)), enrollment__active=True
                ).distinct()
        from django.db.models import Value
        from django.db.models.functions import Coalesce, Lower, NullIf

        ctx["students"] = qs.annotate(
            sort_first=Lower(
                Coalesce(NullIf("preferred_first_name", Value("")), "legal_first_name")
            ),
            sort_last=Lower("last_name"),
        ).order_by("sort_first", "sort_last")
        # pass program_id for form to preserve filter
        ctx["filter_program_id"] = program_id
        return ctx


class BadgeCreateView(LoginRequiredMixin, BadgeManagePermissionMixin, CreateView):
    model = Badge
    form_class = BadgeForm
    template_name = "badges/badge_form.html"
    success_url = reverse_lazy("badges:list")

    def form_valid(self, form):
        resp = super().form_valid(form)
        log_event(
            event=AuditEvent.BADGE_CREATED,
            resource=self.object,
            request=self.request,
            after={
                "name": self.object.name,
                "level": self.object.level,
                "category": self.object.category,
            },
            notes=f"Badge created: {self.object}",
        )
        return resp


class BadgeUpdateView(LoginRequiredMixin, BadgeManagePermissionMixin, UpdateView):
    model = Badge
    form_class = BadgeForm
    template_name = "badges/badge_form.html"
    success_url = reverse_lazy("badges:list")

    def form_valid(self, form):
        before = {"name": self.get_object().name, "level": self.get_object().level}
        resp = super().form_valid(form)
        log_event(
            event=AuditEvent.BADGE_UPDATED,
            resource=self.object,
            request=self.request,
            before=before,
            after={"name": self.object.name, "level": self.object.level},
            notes=f"Badge updated: {self.object}",
        )
        return resp


class BadgeDeleteView(LoginRequiredMixin, BadgeManagePermissionMixin, DeleteView):
    model = Badge
    template_name = "badges/badge_confirm_delete.html"
    success_url = reverse_lazy("badges:list")

    def form_valid(self, form):
        log_event(
            event=AuditEvent.BADGE_DELETED,
            resource=self.get_object(),
            request=self.request,
            before={"name": self.get_object().name, "level": self.get_object().level},
            notes=f"Badge deleted: {self.get_object()}",
        )
        return super().form_valid(form)


class BadgeAwardView(LoginRequiredMixin, BadgeAwardPermissionMixin, View):
    def post(self, request, pk):
        badge = get_object_or_404(Badge, pk=pk)
        student_id = request.POST.get("student_id")
        student = get_object_or_404(Student, pk=student_id)
        obj, created = StudentBadge.objects.get_or_create(
            student=student, badge=badge, defaults={"awarded_by": request.user}
        )
        if created:
            log_event(
                event=AuditEvent.BADGE_AWARDED,
                resource=obj,
                request=request,
                after={
                    "badge": str(badge),
                    "badge_id": badge.pk,
                    "student": str(student),
                    "student_id": student.pk,
                    "awarded_by": str(request.user),
                },
                notes=f"Granted badge '{badge}' (ID {badge.pk}) to student '{student}' (ID {student.pk}) by {request.user}",
            )
            messages.success(request, f"Awarded {badge} to {student}")
        else:
            messages.info(request, "Already awarded")
        return redirect("badges:detail", pk=badge.pk)


class BadgeRevokeView(LoginRequiredMixin, BadgeManagePermissionMixin, View):
    def post(self, request, pk):
        badge = get_object_or_404(Badge, pk=pk)
        student_id = request.POST.get("student_id")
        sb = get_object_or_404(StudentBadge, badge=badge, student_id=student_id)
        log_event(
            event=AuditEvent.BADGE_REVOKED,
            resource=sb,
            request=request,
            before={
                "badge": str(badge),
                "badge_id": badge.pk,
                "student": str(sb.student),
                "student_id": sb.student.pk,
            },
            notes=f"Revoked badge '{badge}' (ID {badge.pk}) from student '{sb.student}' (ID {sb.student.pk}) by {request.user}",
        )
        sb.delete()
        messages.success(request, "Badge revoked")
        return redirect("badges:detail", pk=badge.pk)
