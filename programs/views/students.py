from django.contrib import messages
from django.contrib.auth.mixins import (
    LoginRequiredMixin,
    PermissionRequiredMixin,
)
from django.db.models import Value
from django.db.models.functions import Coalesce, Lower, NullIf
from django.http import HttpResponseRedirect, QueryDict
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.generic import (
    CreateView,
    DetailView,
    ListView,
    UpdateView,
    View,
)

from audit.mixins import SensitiveDataViewMixin

from ..constants import RELATIONSHIP_CHOICES
from ..forms import StudentForm
from ..models import (
    Adult,
    AdultStudentRelationship,
    Program,
    Student,
)
from ..permission_views import (
    LeadMentorRequiredMixin,
    MentorOrLeadMentorRequiredMixin,
    PassUserToFormMixin,
    get_user_role,
)
from ..utils import (
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
    StudentQuerysetRoleMixin,
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
        qs = super().get_queryset()
        program_id = self.kwargs.get("program_id")
        if program_id:
            qs = qs.filter(enrollment__program_id=program_id).distinct()

        qs = self.filter_students_by_role(qs)

        qs = qs.select_related(
            "school",
            "primary_contact_relationship__adult",
            "secondary_contact_relationship__adult",
        ).prefetch_related("adults", "enrollment_set__program")

        # Order by preferred/display name if present, otherwise legal first name, then last name (case-insensitive)
        qs = qs.annotate(
            sort_first=Coalesce(NullIf("first_name", Value("")), "legal_first_name"),
        )
        return self.apply_sorting(qs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        program_id = self.kwargs.get("program_id")
        if program_id:
            ctx["program"] = get_object_or_404(Program, pk=program_id)
        return ctx


class StudentPhotoListView(LoginRequiredMixin, StudentQuerysetRoleMixin, ListView):
    model = Student
    template_name = "students/photo_grid.html"
    context_object_name = "students"
    paginate_by = 48

    def get_queryset(self):
        qs = super().get_queryset()

        qs = self.filter_students_by_role(qs)

        # Order by preferred/display name if present, otherwise legal first name, then last name (case-insensitive)
        return qs.annotate(
            sort_first=Coalesce(NullIf("first_name", Value("")), "legal_first_name"),
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
        qs = super().get_queryset().filter(graduated=False)

        qs = self.filter_students_by_role(qs)

        qs = (
            qs.select_related(
                "school",
                "primary_contact_relationship__adult",
                "secondary_contact_relationship__adult",
            )
            .prefetch_related("adults")
            .annotate(
                sort_first=Coalesce(
                    NullIf("first_name", Value("")), "legal_first_name"
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
        qs = super().get_queryset().filter(graduated=False)

        qs = self.filter_students_by_role(qs)

        return (
            qs.select_related("school")
            .annotate(
                sort_first=Coalesce(
                    NullIf("first_name", Value("")), "legal_first_name"
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
        qs = super().get_queryset().filter(graduated=False)

        role = get_user_role(self.request.user)
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
                    NullIf("first_name", Value("")), "legal_first_name"
                ),
            )
            .order_by("school__name", Lower("sort_first"), Lower("last_name"))
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        ctx["role"] = get_user_role(self.request.user)
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

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .select_related(
                "school",
                "primary_contact_relationship__adult",
                "secondary_contact_relationship__adult",
            )
            .prefetch_related(
                "adults",
                "adultstudentrelationship_set",
                "background_checks",
                "race_ethnicities",
                "signed_documents__program_document",
            )
        )

    def get_object(self, queryset=None):
        if getattr(self, "object", None) is None:
            self.object = super().get_object(queryset)
        return self.object

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        ctx["role"] = get_user_role(self.request.user)
        student = self.object
        # Union of enabled feature keys across all enrolled programs
        keys = set(
            k
            for k in student.programs.values_list("features__key", flat=True).distinct()
            if k
        )
        ctx["program_feature_keys"] = keys
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

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        ctx["role"] = get_user_role(self.request.user)
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
            ctx["enrollments"] = student.enrollment_set.all().select_related("program")
        return ctx

    def form_valid(self, form):
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
                AdultStudentRelationship.objects.update_or_create(
                    adult_id=pid,
                    student=self.object,
                    defaults={"relationship_to_student": rel},
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
    PermissionRequiredMixin,
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

        ctx["role"] = get_user_role(self.request.user)
        ctx["RELATIONSHIP_CHOICES"] = RELATIONSHIP_CHOICES
        return ctx

    def form_valid(self, form):
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
                AdultStudentRelationship.objects.update_or_create(
                    adult_id=pid,
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

        student = get_object_or_404(Student, pk=pk)
        _adult, created, _marked = convert_student_to_alumni(student)
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
        from ..utils import convert_student_to_alumni, find_matching_alumni_adult

        year = request.GET.get("year")
        try:
            year = int(year) if year else timezone.now().year
        except ValueError:
            year = timezone.now().year
        # Default to seniors: graduation_year equals the selected year, and active (non-graduated)
        students = (
            Student.objects.filter(graduation_year=year, graduated=False)
            .annotate(
                sort_first=Coalesce(NullIf("first_name", Value("")), "legal_first_name")
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

        qs = Student.objects.filter(pk__in=ids).order_by("last_name", "first_name")

        if action == "preview":
            # Build preview info without writing changes against Adults flagged as alumni
            will_create = []
            already_alumni = []
            for s in qs:
                if find_matching_alumni_adult(s):
                    already_alumni.append(s)
                else:
                    will_create.append(s)
            will_mark_graduated = [s for s in qs if not s.graduated]
            return render(
                request,
                "students/convert_to_alumni_preview.html",
                {
                    "year": year,
                    "students": qs,
                    "will_create": will_create,
                    "already_alumni": already_alumni,
                    "will_mark_graduated": will_mark_graduated,
                    "ids": ids,
                },
            )

        # Default: perform conversion
        created = 0
        existed = 0
        marked_graduated = 0

        for student in qs:
            _adult, was_created, was_marked = convert_student_to_alumni(student)
            if was_created:
                created += 1
            else:
                existed += 1
            if was_marked:
                marked_graduated += 1
        messages.success(
            request,
            f"Converted {created} new alumni (Adults), {existed} already existed/updated. "
            f"Marked {marked_graduated} student(s) as graduated.",
        )
        return redirect("alumni_list")
