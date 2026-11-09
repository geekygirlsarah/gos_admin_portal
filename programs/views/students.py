import logging
import mimetypes
import os
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Prefetch, Value
from django.db.models.functions import Coalesce, Lower, NullIf
from django.http import (
    FileResponse,
    Http404,
    HttpResponse,
    HttpResponseRedirect,
    QueryDict,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.http import content_disposition_header
from django.views.generic import (
    CreateView,
    DetailView,
    FormView,
    ListView,
    UpdateView,
    View,
)

from applications.rate_limiting import rate_limited_response
from audit.mixins import SensitiveDataViewMixin

from ..constants import RELATIONSHIP_CHOICES
from ..forms import StudentForm, StudentMergeForm
from ..models import (
    Adult,
    AdultStudentRelationship,
    BackgroundCheck,
    EmailDeliveryEvent,
    Enrollment,
    Program,
    Student,
    StudentDocument,
)
from ..permission_views import (
    LeadMentorRequiredMixin,
    PassUserToFormMixin,
    can_user_read,
    get_user_role,
    user_is_mentor_or_lead,
    user_is_parent,
)
from ..utils import convert_student_to_alumni, get_safe_url, redirect_back
from ..utils.merges import (
    _carry_over_missing_student_fields,
    _transfer_student_related_records,
    _transfer_student_relationships,
    perform_student_merge,
)
from ..utils.profile_rate_limit import (
    RateLimitCacheUnavailable,
    check_profile_rate_limit,
)
from .mixins import (
    BackgroundChecksInlineMixin,
    DynamicPermissionMixin,
    DynamicReadPermissionMixin,
    DynamicWritePermissionMixin,
    LogFormSaveMixin,
    SortableListViewMixin,
    StudentQuerysetRoleMixin,
    TenantLeadMentorPermissionRequiredMixin,
)


def _tenant_student_queryset(organization):
    if organization is None:
        return Student.objects.none()
    return Student.objects.for_organization(organization).filter(
        (
            models.Q(primary_contact_relationship__isnull=True)
            | models.Q(
                primary_contact_relationship__organization=organization,
                primary_contact_relationship__adult__organization=organization,
                primary_contact_relationship__student_id=models.F("pk"),
            )
        ),
        (
            models.Q(secondary_contact_relationship__isnull=True)
            | models.Q(
                secondary_contact_relationship__organization=organization,
                secondary_contact_relationship__adult__organization=organization,
                secondary_contact_relationship__student_id=models.F("pk"),
            )
        ),
    )


class StudentListView(
    LoginRequiredMixin,
    DynamicReadPermissionMixin,
    SortableListViewMixin,
    StudentQuerysetRoleMixin,
    ListView,
):
    model = Student
    template_name = "students/list.html"
    context_object_name = "students"
    section = "student_info"

    sort_fields = {
        "name": (Lower("sort_first"), Lower("last_name")),
        "school": Lower("school__name"),
        "graduation_year": "graduation_year",
        "graduated": "graduated",
    }
    default_sort_field = "name"

    def get_queryset(self):
        organization = getattr(self.request, "organization", None)
        qs = super().get_queryset().for_organization(organization)
        program_id = self.kwargs.get("program_id")
        if program_id:
            qs = qs.filter(enrollment__program_id=program_id).distinct()

        qs = self.filter_students_by_role(qs)

        email_delivery_events = (
            EmailDeliveryEvent.objects.verified_for_organization(organization)
            .filter(
                resolved=False,
                event_type__in=[
                    EmailDeliveryEvent.EVENT_BOUNCED,
                    EmailDeliveryEvent.EVENT_COMPLAINT,
                ],
            )
            .select_related(
                "student", "adult", "application__program", "trusted_mapping"
            )
        )
        qs = qs.select_related(
            "school",
            "primary_contact_relationship__adult",
            "secondary_contact_relationship__adult",
        ).prefetch_related(
            "adults",
            "enrollment_set__program",
            Prefetch("email_delivery_events", queryset=email_delivery_events),
        )

        # Order by preferred/display name if present, otherwise legal first name, then last name (case-insensitive)
        qs = qs.annotate(
            sort_first=Coalesce(
                NullIf("preferred_first_name", Value("")), "legal_first_name"
            ),
        )
        return self.apply_sorting(qs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        program_id = self.kwargs.get("program_id")
        if program_id:
            ctx["program"] = get_object_or_404(
                Program.objects.for_organization(self.request.organization),
                pk=program_id,
            )

        students = list(ctx.get("students") or ctx.get("object_list") or [])
        ctx["total_students_count"] = len(students)
        ctx["active_students_count"] = sum(1 for s in students if not s.graduated)
        ctx["graduated_count"] = sum(1 for s in students if s.graduated)
        ctx["unique_schools_count"] = len(
            set(s.school_id for s in students if s.school_id)
        )
        return ctx


class StudentPhotoListView(LoginRequiredMixin, StudentQuerysetRoleMixin, ListView):
    model = Student
    template_name = "students/photo_grid.html"
    context_object_name = "students"
    paginate_by = 48

    def get_queryset(self):
        qs = (
            super()
            .get_queryset()
            .for_organization(getattr(self.request, "organization", None))
        )

        qs = self.filter_students_by_role(qs)

        # Order by preferred/display name if present, otherwise legal first name, then last name (case-insensitive)
        return qs.annotate(
            sort_first=Coalesce(
                NullIf("preferred_first_name", Value("")), "legal_first_name"
            ),
        ).order_by(Lower("sort_first"), Lower("last_name"))


class StudentEmergencyContactsView(
    LoginRequiredMixin, SortableListViewMixin, StudentQuerysetRoleMixin, ListView
):
    model = Student
    template_name = "students/emergency_contacts.html"
    context_object_name = "students"

    sort_fields = {
        "name": (Lower("sort_first"), Lower("last_name")),
        "school": Lower("school__name"),
    }
    default_sort_field = "name"

    def get_queryset(self):
        organization = getattr(self.request, "organization", None)
        if organization is None:
            return Student.objects.none()
        relationships = (
            AdultStudentRelationship.objects.for_organization(organization)
            .filter(
                adult__organization=organization,
                student__organization=organization,
            )
            .select_related("adult", "student")
        )
        parents = Adult.objects.for_organization(organization).filter(
            pk__in=relationships.values("adult_id")
        )
        qs = (
            super()
            .get_queryset()
            .for_organization(organization)
            .filter(graduated=False)
        )

        qs = self.filter_students_by_role(qs)

        qs = (
            qs.select_related(
                "school",
                "primary_contact_relationship__adult",
                "secondary_contact_relationship__adult",
            )
            .prefetch_related(
                Prefetch("adults", queryset=parents),
                Prefetch("adultstudentrelationship_set", queryset=relationships),
            )
            .annotate(
                sort_first=Coalesce(
                    NullIf("preferred_first_name", Value("")), "legal_first_name"
                ),
            )
        )
        return self.apply_sorting(qs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        # Backwards compatibility: some templates expect 'plist'
        ctx.setdefault("plist", ctx.get("students") or ctx.get("object_list"))
        return ctx


class StudentsByGradeView(LoginRequiredMixin, StudentQuerysetRoleMixin, ListView):
    model = Student
    template_name = "students/by_grade.html"
    context_object_name = "students"

    def get_queryset(self):
        qs = (
            super()
            .get_queryset()
            .for_organization(getattr(self.request, "organization", None))
            .filter(graduated=False)
        )

        qs = self.filter_students_by_role(qs)

        return (
            qs.select_related("school")
            .annotate(
                sort_first=Coalesce(
                    NullIf("preferred_first_name", Value("")), "legal_first_name"
                ),
            )
            .order_by("graduation_year", Lower("sort_first"), Lower("last_name"))
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        current_year = timezone.now().year

        def compute_grade(gy):
            if not gy:
                return None
            # Approximate US grade level: graduating this year = 12th grade now
            return 12 - (gy - current_year)

        def label_for_grade(grade_num):
            if grade_num is None:
                return "Unknown Grade"
            if grade_num < 0:
                return "Pre-K"
            if grade_num == 0:
                return "Kindergarten"
            # Ordinal suffixes
            n = int(grade_num)
            if 10 <= (n % 100) <= 13:
                suffix = "th"
            else:
                suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
            return f"{n}{suffix} Grade"

        grouped = {}
        grade_order = {}
        for s in ctx["students"]:
            g = compute_grade(getattr(s, "graduation_year", None))
            label = label_for_grade(g)
            grouped.setdefault(label, []).append(s)
            if label not in grade_order:
                grade_order[label] = -1000 if g is None else int(g)
        # Sort labels by grade number descending (12 -> 11 -> ... -> 1 -> 0 -> negatives), Unknown last
        sorted_labels = sorted(
            grade_order.keys(), key=lambda lbl: grade_order[lbl], reverse=True
        )
        # Ensure 'Unknown Grade' is at the very end
        if "Unknown Grade" in sorted_labels:
            sorted_labels = [lbl for lbl in sorted_labels if lbl != "Unknown Grade"] + [
                "Unknown Grade"
            ]
        ctx["grouped"] = [(label, grouped.get(label, [])) for label in sorted_labels]
        return ctx


class StudentsBySchoolView(LoginRequiredMixin, ListView):
    model = Student
    template_name = "students/by_school.html"
    context_object_name = "students"

    def get_queryset(self):
        qs = (
            super()
            .get_queryset()
            .for_organization(getattr(self.request, "organization", None))
            .filter(graduated=False)
        )

        role = get_user_role(self.request.user, org=self.request.organization)
        if role == "Parent":
            try:
                adult = self.request.user.adult_profile
                qs = qs.filter(adults=adult)
            except (Adult.DoesNotExist, AttributeError):
                qs = Student.objects.none()
        elif role == "Student":
            try:
                student = self.request.user.student_profile
                qs = qs.filter(pk=student.pk)
            except (Student.DoesNotExist, AttributeError):
                qs = Student.objects.none()

        return (
            qs.select_related("school")
            .annotate(
                sort_first=Coalesce(
                    NullIf("preferred_first_name", Value("")), "legal_first_name"
                ),
            )
            .order_by("school__name", Lower("sort_first"), Lower("last_name"))
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        ctx["role"] = get_user_role(self.request.user, org=self.request.organization)
        grouped = {}
        for s in ctx["students"]:
            label = s.school.name if s.school_id else "No School"
            grouped.setdefault(label, []).append(s)
        # Sort by school label
        ctx["grouped"] = sorted(
            grouped.items(), key=lambda kv: (kv[0] == "No School", kv[0])
        )
        return ctx


class StudentDetailView(
    DynamicPermissionMixin, SensitiveDataViewMixin, LoginRequiredMixin, DetailView
):
    model = Student
    template_name = "students/detail.html"
    context_object_name = "student"
    section = "student_info"

    def get(self, request, *args, **kwargs):
        organization = getattr(request, "organization", None)
        if (
            organization is not None
            and not request.user.is_superuser
            and get_user_role(request.user, org=organization) == "Mentor"
        ):
            try:
                allowed, retry_after = check_profile_rate_limit(
                    request.user.pk, organization.pk
                )
            except RateLimitCacheUnavailable:
                logging.getLogger(__name__).exception(
                    "Profile rate-limit cache unavailable "
                    "(user_id=%s, organization_id=%s)",
                    request.user.pk,
                    organization.pk,
                )
                return HttpResponse(
                    "Profile access is temporarily unavailable. Please try again shortly.",
                    status=503,
                )

            if not allowed:
                return rate_limited_response(
                    request,
                    retry_after,
                    return_url=reverse("student_list"),
                    return_label="Back to the student directory",
                )

        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        from django.db.models import Prefetch

        from ..models import ProgramFeature

        organization = getattr(self.request, "organization", None)
        if organization is None:
            return Student.objects.none()

        email_delivery_events = (
            EmailDeliveryEvent.objects.verified_for_organization(organization)
            .filter(
                resolved=False,
                event_type__in=[
                    EmailDeliveryEvent.EVENT_BOUNCED,
                    EmailDeliveryEvent.EVENT_COMPLAINT,
                ],
            )
            .select_related(
                "student", "adult", "application__program", "trusted_mapping"
            )
        )

        return (
            _tenant_student_queryset(organization)
            .select_related(
                "school",
                "primary_contact_relationship__adult",
                "secondary_contact_relationship__adult",
            )
            .prefetch_related(
                Prefetch(
                    "adults",
                    queryset=Adult.objects.for_organization(organization),
                ),
                Prefetch(
                    "adultstudentrelationship_set",
                    queryset=AdultStudentRelationship.objects.for_organization(
                        organization
                    )
                    .filter(
                        adult__organization=organization,
                        student__organization=organization,
                    )
                    .select_related("adult", "student"),
                    to_attr="tenant_contact_relationships",
                ),
                Prefetch(
                    "background_checks",
                    queryset=BackgroundCheck.objects.for_organization(organization)
                    .filter(
                        student__organization=organization,
                        check_type__organization=organization,
                    )
                    .select_related("check_type"),
                ),
                "race_ethnicities",
                Prefetch(
                    "signed_documents",
                    queryset=StudentDocument.objects.filter(
                        program_document__program__organization=organization
                    ).select_related("program_document"),
                ),
                Prefetch(
                    "enrollment_set",
                    queryset=Enrollment.objects.for_organization(organization)
                    .filter(program__organization=organization)
                    .select_related("program", "team", "crew", "subteam")
                    .prefetch_related(
                        Prefetch(
                            "program__features",
                            queryset=ProgramFeature.objects.for_organization(
                                organization
                            ),
                        )
                    ),
                ),
                Prefetch("email_delivery_events", queryset=email_delivery_events),
            )
        )

    def get_object(self, queryset=None):
        if getattr(self, "object", None) is None:
            self.object = super().get_object(queryset)
        return self.object

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        ctx["role"] = get_user_role(self.request.user, org=self.request.organization)
        student = self.object
        enrollments = list(student.enrollment_set.all())
        keys = set()
        for e in enrollments:
            for f in e.program.features.all():
                if f.key:
                    keys.add(f.key)
        ctx["program_feature_keys"] = keys
        ctx["enrollments"] = enrollments

        # Active vs other enrollments
        active_enrollments = []
        other_enrollments = []
        for e in enrollments:
            if e.program.active and e.active:
                active_enrollments.append(e)
            else:
                other_enrollments.append(e)
        ctx["active_enrollments"] = active_enrollments
        ctx["other_enrollments"] = other_enrollments

        # Attendance stats aggregation (only if any program has attendance feature)
        has_attendance = "attendance" in keys
        if has_attendance:
            from django.db.models import Case, F, IntegerField, Sum, When

            now = timezone.now()
            start_of_week = (now - timedelta(days=now.weekday())).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            from attendance.models import AttendanceSession

            organization = self.request.organization
            sessions_qs = AttendanceSession.objects.filter(
                student=student,
                organization=organization,
                program__organization=organization,
            )
            rows = list(
                sessions_qs.values("program_id").annotate(
                    total=Sum("duration_minutes"),
                    week_total=Sum(
                        Case(
                            When(
                                check_in__gte=start_of_week,
                                then=F("duration_minutes"),
                            ),
                            default=0,
                            output_field=IntegerField(),
                        )
                    ),
                )
            )
            program_stats = {}
            total_mins = 0
            week_mins = 0
            for row in rows:
                p_total = row["total"] or 0
                p_week = row["week_total"] or 0
                total_mins += p_total
                week_mins += p_week
                program_stats[row["program_id"]] = {
                    "total_hours": round(p_total / 60.0, 1),
                    "week_hours": round(p_week / 60.0, 1),
                }

            ctx["attendance_total_hours"] = round(total_mins / 60.0, 1)
            ctx["attendance_week_hours"] = round(week_mins / 60.0, 1)
            for e in enrollments:
                e.has_attendance = "attendance" in [
                    f.key for f in e.program.features.all()
                ]
                e.attendance_stats = program_stats.get(
                    e.program_id, {"total_hours": 0.0, "week_hours": 0.0}
                )
        else:
            ctx["attendance_total_hours"] = 0.0
            ctx["attendance_week_hours"] = 0.0
            for e in enrollments:
                e.has_attendance = False
                e.attendance_stats = {"total_hours": 0.0, "week_hours": 0.0}

        # Outreach stats & upcoming signups
        has_outreach = "outreach" in keys
        ctx["has_outreach_feature"] = has_outreach
        if has_outreach:
            from outreach.models import OutreachSignup
            from outreach.utils import compute_outreach_stats

            outreach_signups = list(
                OutreachSignup.objects.filter(
                    student=student,
                    organization=self.request.organization,
                    shift__event__organization=self.request.organization,
                )
                .select_related("shift", "shift__event", "shift__event__program")
                .order_by("shift__date", "shift__start_time")
            )
            ctx["outreach_stats"] = compute_outreach_stats(outreach_signups)
            ctx["upcoming_outreach_signups"] = [
                s for s in outreach_signups if not s.shift.is_past
            ]
        else:
            ctx["outreach_stats"] = {
                "total_outreach_hours": 0.0,
                "pending_outreach_hours": 0.0,
                "championed_count": 0,
            }
            ctx["upcoming_outreach_signups"] = []

        # Badges earned
        if "badges" in keys:
            try:
                from badges.models import StudentBadge

                badges_earned = list(
                    StudentBadge.objects.filter(
                        student=student,
                        organization=self.request.organization,
                        badge__organization=self.request.organization,
                    ).select_related("badge", "awarded_by")
                )
                ctx["badges_earned"] = badges_earned
                ctx["badges_count"] = len(badges_earned)
            except ImportError:
                ctx["badges_earned"] = []
                ctx["badges_count"] = 0
        else:
            ctx["badges_earned"] = []
            ctx["badges_count"] = 0

        return ctx


class StudentUpdateView(
    PassUserToFormMixin,
    SensitiveDataViewMixin,
    LogFormSaveMixin,
    LoginRequiredMixin,
    DynamicWritePermissionMixin,
    BackgroundChecksInlineMixin,
    UpdateView,
):
    model = Student
    form_class = StudentForm
    template_name = "students/form.html"
    permission_required = "programs.change_student"
    section = "student_info"
    background_checks_kwarg = "student"

    def get_queryset(self):
        return _tenant_student_queryset(getattr(self.request, "organization", None))

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        ctx["role"] = get_user_role(self.request.user, org=self.request.organization)
        ctx["RELATIONSHIP_CHOICES"] = RELATIONSHIP_CHOICES
        student = self.object
        # Union of enabled feature keys across all enrolled programs
        keys = (
            set(
                k
                for k in student.programs.values_list(
                    "features__key", flat=True
                ).distinct()
                if k
            )
            if student
            else set()
        )
        ctx["program_feature_keys"] = keys
        if student:
            ctx["enrollments"] = (
                Enrollment.objects.for_organization(self.request.organization)
                .filter(
                    student=student,
                    program__organization=self.request.organization,
                )
                .select_related("program")
            )
        return ctx

    def form_valid(self, form):
        response = super().form_valid(form)
        # Persist relationship selections for each selected parent
        rel_map = {
            k[len("parent_rel_") :]: v  # noqa: E203
            for k, v in self.request.POST.items()
            if k.startswith("parent_rel_")
        }
        specific_map = {
            k[len("parent_specific_rel_") :]: v  # noqa: E203
            for k, v in self.request.POST.items()
            if k.startswith("parent_specific_rel_")
        }
        valid_keys = set(k for k, _ in RELATIONSHIP_CHOICES)
        organization = getattr(self.request, "organization", None)
        for pid_str, rel in rel_map.items():
            try:
                pid = int(pid_str)
            except (TypeError, ValueError):
                continue
            defaults = {}
            if rel in valid_keys:
                defaults["relationship_to_student"] = rel
            specific = specific_map.get(pid_str, "")
            if specific:
                defaults["specific_relationship"] = specific
            adult = (
                Adult.objects.for_organization(organization)
                .filter(pk=pid, is_parent=True)
                .first()
            )
            if defaults and adult is not None:
                AdultStudentRelationship.objects.update_or_create(
                    organization=organization,
                    adult=adult,
                    student=self.object,
                    defaults=defaults,
                )
        messages.success(self.request, "Student record saved successfully.")
        return response

    def get_success_url(self):
        next_url = self.request.GET.get("next")
        safe_url = get_safe_url(self.request, next_url)
        if safe_url:
            return safe_url
        return reverse("student_detail", args=[self.object.pk])


class StudentCreateView(
    PassUserToFormMixin,
    LogFormSaveMixin,
    LoginRequiredMixin,
    TenantLeadMentorPermissionRequiredMixin,
    DynamicWritePermissionMixin,
    CreateView,
):
    model = Student
    form_class = StudentForm
    template_name = "students/form.html"
    permission_required = "programs.add_student"
    section = "student_info"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        ctx["role"] = get_user_role(self.request.user, org=self.request.organization)
        ctx["RELATIONSHIP_CHOICES"] = RELATIONSHIP_CHOICES
        return ctx

    def form_valid(self, form):
        organization = getattr(self.request, "organization", None)
        form.instance.organization = organization
        response = super().form_valid(form)
        # Persist relationship selections for each selected parent
        rel_map = {
            k[len("parent_rel_") :]: v  # noqa: E203
            for k, v in self.request.POST.items()
            if k.startswith("parent_rel_")
        }
        valid_keys = set(k for k, _ in RELATIONSHIP_CHOICES)
        for pid_str, rel in rel_map.items():
            try:
                pid = int(pid_str)
            except (TypeError, ValueError):
                continue
            if rel in valid_keys:
                adult = (
                    Adult.objects.for_organization(organization)
                    .filter(pk=pid, is_parent=True)
                    .first()
                )
                if adult is not None:
                    AdultStudentRelationship.objects.update_or_create(
                        organization=organization,
                        adult=adult,
                        student=self.object,
                        defaults={"relationship_to_student": rel},
                    )
        return response

    def get_success_url(self):
        # After creating a Student, return to the Students listing
        return reverse("student_list")


class StudentConvertToAlumniView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "programs.change_student"

    def post(self, request, pk):
        from ..utils import convert_student_to_alumni

        organization = getattr(request, "organization", None)
        if organization is None:
            raise Http404
        student = get_object_or_404(
            Student.objects.for_organization(organization), pk=pk
        )
        try:
            _adult, created, _marked = convert_student_to_alumni(student)
        except ValidationError:
            messages.error(
                request,
                "This student has inconsistent organization data and could not be converted.",
            )
            return redirect_back(request, "student_list")
        if created:
            messages.success(
                request,
                f"Converted {student} to Alumni (Adult created) and marked student as graduated.",
            )
        else:
            messages.info(
                request,
                f"{student} is now marked as Alumni. Student marked as graduated.",
            )
        return redirect_back(request, "student_list")


class StudentBulkConvertToAlumniView(LoginRequiredMixin, LeadMentorRequiredMixin, View):
    template_name = "students/convert_to_alumni.html"

    def get(self, request):
        organization = getattr(request, "organization", None)
        if organization is None:
            raise Http404
        year = request.GET.get("year")
        try:
            year = int(year) if year else timezone.now().year
        except ValueError:
            year = timezone.now().year
        # Default to seniors: graduation_year equals the selected year, and active (non-graduated)
        students = (
            Student.objects.for_organization(organization)
            .filter(graduation_year=year, graduated=False)
            .annotate(
                sort_first=Coalesce(
                    NullIf("preferred_first_name", Value("")), "legal_first_name"
                )
            )
            .order_by(Lower("sort_first"), Lower("last_name"))
        )
        return render(
            request,
            self.template_name,
            {
                "year": year,
                "students": students,
            },
        )

    def post(self, request):
        from ..utils import convert_student_to_alumni, find_matching_alumni_adult

        organization = getattr(request, "organization", None)
        if organization is None:
            raise Http404
        action = request.POST.get("action", "convert")
        ids = request.POST.getlist("student_ids")
        year = request.POST.get("year")

        # Validate and normalize year
        try:
            if year:
                year = str(int(year))
        except (ValueError, TypeError):
            year = None

        if not ids:
            messages.info(request, "No students selected.")
            if year:
                base_url = reverse("student_bulk_convert_select")
                query = QueryDict("", mutable=True)
                query["year"] = year
                return HttpResponseRedirect(f"{base_url}?{query.urlencode()}")
            return redirect("student_bulk_convert_select")

        qs = (
            Student.objects.for_organization(organization)
            .filter(pk__in=ids)
            .order_by("last_name", "preferred_first_name")
        )

        if action == "preview":
            # Build preview info without writing changes against Adults flagged as alumni
            will_create = []
            already_alumni = []
            blocked = []
            for s in qs:
                try:
                    if find_matching_alumni_adult(s):
                        already_alumni.append(s)
                    else:
                        will_create.append(s)
                except ValidationError:
                    blocked.append(s)
            will_mark_graduated = [s for s in qs if not s.graduated]
            if blocked:
                messages.error(
                    request,
                    f"{len(blocked)} selected student(s) have inconsistent organization data and cannot be converted.",
                )
            return render(
                request,
                "students/convert_to_alumni_preview.html",
                {
                    "year": year,
                    "students": qs,
                    "will_create": will_create,
                    "already_alumni": already_alumni,
                    "will_mark_graduated": will_mark_graduated,
                    "blocked": blocked,
                    "ids": ids,
                },
            )

        # Default: perform conversion
        created = 0
        existed = 0
        marked_graduated = 0

        try:
            with transaction.atomic():
                for student in qs:
                    _adult, was_created, was_marked = convert_student_to_alumni(student)
                    if was_created:
                        created += 1
                    else:
                        existed += 1
                    if was_marked:
                        marked_graduated += 1
        except ValidationError:
            messages.error(
                request,
                "One or more selected students have inconsistent organization data. No conversions were made.",
            )
            return redirect("student_bulk_convert_select")
        messages.success(
            request,
            f"Converted {created} new alumni (Adults), {existed} already existed/updated. "
            f"Marked {marked_graduated} student(s) as graduated.",
        )
        return redirect("alumni_list")


class StudentMergeView(LoginRequiredMixin, LeadMentorRequiredMixin, FormView):
    template_name = "students/merge.html"
    form_class = StudentMergeForm
    success_url = reverse_lazy("student_list")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["organization"] = getattr(self.request, "organization", None)
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from django.db.models import Prefetch

        organization = getattr(self.request, "organization", None)
        students = _tenant_student_queryset(organization)
        if organization is None:
            enrollments = Enrollment.objects.none()
            relationships = AdultStudentRelationship.objects.none()
        else:
            enrollments = (
                Enrollment.objects.for_organization(organization)
                .filter(
                    student__organization=organization,
                    program__organization=organization,
                )
                .select_related("program")
            )
            relationships = (
                AdultStudentRelationship.objects.for_organization(organization)
                .filter(
                    adult__organization=organization,
                    student__organization=organization,
                )
                .select_related("adult")
            )
        context["students"] = list(
            students.select_related(
                "school",
                "primary_contact_relationship__adult",
                "secondary_contact_relationship__adult",
            )
            .prefetch_related(
                Prefetch("enrollment_set", queryset=enrollments),
                Prefetch("adultstudentrelationship_set", queryset=relationships),
            )
            .annotate(
                sort_first=Coalesce(
                    NullIf("preferred_first_name", Value("")), "legal_first_name"
                ),
            )
            .order_by(Lower("sort_first"), Lower("last_name"))
        )
        return context

    def form_valid(self, form):
        from audit.events import AuditEvent
        from audit.service import log_event

        keep = form.cleaned_data["keep"]
        source = form.cleaned_data["source"]

        try:
            meta = perform_student_merge(keep, source)
        except ValidationError:
            form.add_error(
                None,
                "These records have inconsistent organization data and cannot be merged.",
            )
            return self.form_invalid(form)

        log_event(
            request=self.request,
            event=AuditEvent.RECORDS_MERGED,
            resource=keep,
            notes=(
                f'Student "{meta["source_name"]}" (pk={meta["source_pk"]}) merged into '
                f'"{meta["keep_name"]}" (pk={meta["keep_pk"]}). All enrollments, records, and relationships '
                f"were transferred."
            ),
        )

        messages.success(
            self.request,
            f'Merged "{meta["source_name"]}" into "{meta["keep_name"]}". '
            f"All enrollments, records, and relationships were transferred.",
        )
        return super().form_valid(form)


class StudentDocumentDownloadView(LoginRequiredMixin, View):
    """Download/view a signed StudentDocument.

    Accessible to LeadMentors, Mentors who can view the student/program,
    linked Parents of the student, and the Student themselves.
    """

    def get(self, request, pk):
        organization = getattr(request, "organization", None)
        if organization is None:
            raise Http404
        student_doc = get_object_or_404(
            StudentDocument.objects.filter(
                student__organization=organization,
                program_document__program__organization=organization,
            ),
            pk=pk,
        )
        student = student_doc.student
        user = request.user

        role = get_user_role(user, org=organization)
        has_access = False

        if role == "LeadMentor" or user.is_superuser:
            has_access = True
        elif user_is_parent(user, org=organization):
            adult = getattr(user, "adult_profile", None)
            if (
                adult
                and adult.is_parent
                and adult.organization_id == organization.pk
                and AdultStudentRelationship.objects.for_organization(organization)
                .filter(adult=adult, student=student)
                .exists()
            ):
                has_access = True
        elif hasattr(user, "student_profile") and user.student_profile == student:
            has_access = True
        elif user_is_mentor_or_lead(user):
            if can_user_read(
                user, "students", student, org=organization
            ) or can_user_read(
                user,
                "programs",
                student_doc.program_document.program,
                org=organization,
            ):
                has_access = True

        if not has_access:
            messages.error(request, "You do not have permission to view this document.")
            return redirect("home")

        filename = os.path.basename(student_doc.file.name)
        content_type, _ = mimetypes.guess_type(filename)
        content_type = content_type or "application/octet-stream"

        response = FileResponse(student_doc.file.open("rb"), content_type=content_type)
        disposition = "attachment" if request.GET.get("download") else "inline"
        response["Content-Disposition"] = f'{disposition}; filename="{filename}"'
        return response


class StudentPhotoView(LoginRequiredMixin, View):
    """Serve a student's photo only to a user allowed to view that student."""

    def get(self, request, pk):
        organization = getattr(request, "organization", None)
        if organization is None:
            raise Http404
        student = get_object_or_404(
            _tenant_student_queryset(organization).exclude(photo=""), pk=pk
        )
        role = get_user_role(request.user, org=organization)
        if role == "Parent":
            try:
                adult = request.user.adult_profile
            except (Adult.DoesNotExist, AttributeError):
                raise Http404
            if (
                adult.organization_id != organization.pk
                or not AdultStudentRelationship.objects.for_organization(organization)
                .filter(
                    adult=adult,
                    student=student,
                    adult__organization=organization,
                    student__organization=organization,
                )
                .exists()
            ):
                raise Http404
        if not can_user_read(request.user, "student_info", student, org=organization):
            raise Http404

        filename = os.path.basename(student.photo.name)
        content_type, _ = mimetypes.guess_type(filename)
        if content_type not in {
            "image/bmp",
            "image/gif",
            "image/jpeg",
            "image/png",
            "image/tiff",
            "image/webp",
        }:
            content_type = "application/octet-stream"
        response = FileResponse(student.photo.open("rb"), content_type=content_type)
        response["Content-Disposition"] = content_disposition_header(
            content_type.startswith("image/"), filename
        )
        response["X-Content-Type-Options"] = "nosniff"
        response["Cache-Control"] = "private, no-store"
        return response
