from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.db import models, transaction
from django.db.models import Value
from django.db.models.functions import Coalesce, Lower, NullIf
from django.http import HttpResponseRedirect, QueryDict
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.generic import (
    CreateView,
    DetailView,
    FormView,
    ListView,
    UpdateView,
    View,
)

from applications.models import Application
from attendance.models import (
    AttendanceEvent,
    AttendanceSession,
    DigitalSignout,
    RFIDCard,
    StudentPresence,
)
from audit.mixins import SensitiveDataViewMixin
from badges.models import StudentBadge
from outreach.models import OutreachSignup

from ..constants import RELATIONSHIP_CHOICES
from ..forms import StudentForm, StudentMergeForm
from ..models import (
    Adult,
    AdultStudentRelationship,
    BackgroundCheck,
    Enrollment,
    FeeAssignment,
    Payment,
    Program,
    SlidingScale,
    Student,
    StudentDocument,
)
from ..permission_views import (
    LeadMentorRequiredMixin,
    PassUserToFormMixin,
    get_user_role,
)
from ..utils import get_safe_url, redirect_back
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
            sort_first=Coalesce(
                NullIf("preferred_first_name", Value("")), "legal_first_name"
            ),
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
        qs = super().get_queryset().filter(graduated=False)

        qs = self.filter_students_by_role(qs)

        qs = (
            qs.select_related(
                "school",
                "primary_contact_relationship__adult",
                "secondary_contact_relationship__adult",
            )
            .prefetch_related("adults", "adultstudentrelationship_set")
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
        qs = super().get_queryset().filter(graduated=False)

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
                    NullIf("preferred_first_name", Value("")), "legal_first_name"
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
                "enrollment_set__program",
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
        specific_map = {
            k[len("parent_specific_rel_") :]: v  # noqa: E203
            for k, v in self.request.POST.items()
            if k.startswith("parent_specific_rel_")
        }
        valid_keys = set(k for k, _ in RELATIONSHIP_CHOICES)
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
            if defaults:
                AdultStudentRelationship.objects.update_or_create(
                    adult_id=pid,
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
        year = request.GET.get("year")
        try:
            year = int(year) if year else timezone.now().year
        except ValueError:
            year = timezone.now().year
        # Default to seniors: graduation_year equals the selected year, and active (non-graduated)
        students = (
            Student.objects.filter(graduation_year=year, graduated=False)
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

        qs = Student.objects.filter(pk__in=ids).order_by(
            "last_name", "preferred_first_name"
        )

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


def _transfer_student_relationships(keep, source):
    """Move all of ``source``'s adult/parent relationships onto ``keep``."""
    changed = False
    for rel in list(AdultStudentRelationship.objects.filter(student=source)):
        existing = AdultStudentRelationship.objects.filter(
            adult=rel.adult, student=keep
        ).first()
        if existing:
            if not existing.specific_relationship and rel.specific_relationship:
                existing.specific_relationship = rel.specific_relationship
                existing.save(update_fields=["specific_relationship"])
            if (
                source.primary_contact_relationship_id == rel.id
                and not keep.primary_contact_relationship_id
            ):
                keep.primary_contact_relationship = existing
                changed = True
            if (
                source.secondary_contact_relationship_id == rel.id
                and not keep.secondary_contact_relationship_id
            ):
                keep.secondary_contact_relationship = existing
                changed = True
            rel.delete()
        else:
            rel.student = keep
            rel.save(update_fields=["student"])
            if (
                source.primary_contact_relationship_id == rel.id
                and not keep.primary_contact_relationship_id
            ):
                keep.primary_contact_relationship = rel
                changed = True
            if (
                source.secondary_contact_relationship_id == rel.id
                and not keep.secondary_contact_relationship_id
            ):
                keep.secondary_contact_relationship = rel
                changed = True
    return changed


def _transfer_student_related_records(keep, source):
    """Transfer or merge all related models referencing ``source`` onto ``keep``."""
    # 1. Enrollments
    for enr in list(Enrollment.objects.filter(student=source)):
        existing = Enrollment.objects.filter(student=keep, program=enr.program).first()
        if existing:
            updated = False
            if not existing.team_id and enr.team_id:
                existing.team = enr.team
                updated = True
            if not existing.crew_id and enr.crew_id:
                existing.crew = enr.crew
                updated = True
            if not existing.subteam_id and enr.subteam_id:
                existing.subteam = enr.subteam
                updated = True
            if not existing.active and enr.active:
                existing.active = True
                updated = True
            if not existing.clearance_due and enr.clearance_due:
                existing.clearance_due = True
                updated = True
            if updated:
                existing.save()
            enr.delete()
        else:
            enr.student = keep
            enr.save(update_fields=["student"])

    # 2. Fee assignments
    for fa in list(FeeAssignment.objects.filter(student=source)):
        existing = FeeAssignment.objects.filter(student=keep, fee=fa.fee).first()
        if existing:
            if not existing.notes and fa.notes:
                existing.notes = fa.notes
                existing.save(update_fields=["notes"])
            fa.delete()
        else:
            fa.student = keep
            fa.save(update_fields=["student"])

    # 3. Payments
    Payment.objects.filter(student=source).update(student=keep)

    # 4. Sliding scale applications
    SlidingScale.objects.filter(student=source).update(student=keep)

    # 5. Student signed documents
    for doc in list(StudentDocument.objects.filter(student=source)):
        existing = StudentDocument.objects.filter(
            student=keep, program_document=doc.program_document
        ).first()
        if existing:
            if not existing.file and doc.file:
                existing.file = doc.file
                existing.save(update_fields=["file"])
            doc.delete()
        else:
            doc.student = keep
            doc.save(update_fields=["student"])

    # 6. Background checks
    for bg in list(BackgroundCheck.objects.filter(student=source)):
        existing = BackgroundCheck.objects.filter(
            student=keep, check_type=bg.check_type
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
            bg.student = keep
            bg.save(update_fields=["student"])

    # 7. RFID cards
    for card in list(RFIDCard.objects.filter(student=source)):
        if not RFIDCard.objects.filter(student=keep, uid=card.uid).exists():
            card.student = keep
            card.save(update_fields=["student"])
        else:
            card.delete()

    # 8. Attendance records (protected FKs)
    AttendanceSession.objects.filter(student=source).update(student=keep)
    AttendanceEvent.objects.filter(student=source).update(student=keep)

    # 9. Student Presence (attendance per-day)
    for presence in list(StudentPresence.objects.filter(student=source)):
        existing = StudentPresence.objects.filter(
            student=keep, program=presence.program, date=presence.date
        ).first()
        if existing:
            if (
                presence.status == StudentPresence.ABSENT
                and existing.status != StudentPresence.ABSENT
            ):
                existing.status = StudentPresence.ABSENT
                existing.save(update_fields=["status"])
            presence.delete()
        else:
            presence.student = keep
            presence.save(update_fields=["student"])

    # 10. Digital Signouts
    DigitalSignout.objects.filter(student=source).update(student=keep)

    # 11. Applications
    Application.objects.filter(converted_student=source).update(converted_student=keep)

    # 12. Outreach student signups
    for signup in list(OutreachSignup.objects.filter(student=source)):
        existing = OutreachSignup.objects.filter(
            student=keep, shift=signup.shift
        ).first()
        if existing:
            updated = False
            if (
                signup.role == OutreachSignup.CHAMPION
                and existing.role != OutreachSignup.CHAMPION
            ):
                existing.role = OutreachSignup.CHAMPION
                updated = True
            if signup.checked_in_at and not existing.checked_in_at:
                existing.checked_in_at = signup.checked_in_at
                updated = True
            if signup.checked_out_at and not existing.checked_out_at:
                existing.checked_out_at = signup.checked_out_at
                updated = True
            if updated:
                existing.save()
            signup.delete()
        else:
            signup.student = keep
            signup.save(update_fields=["student"])

    # 13. Badges
    for badge_award in list(StudentBadge.objects.filter(student=source)):
        if not StudentBadge.objects.filter(
            student=keep, badge=badge_award.badge
        ).exists():
            badge_award.student = keep
            badge_award.save(update_fields=["student"])
        else:
            badge_award.delete()

    # 14. Alumni profile link on Adult
    for adult in list(Adult.objects.filter(student_record=source)):
        if not Adult.objects.filter(student_record=keep).exists():
            adult.student_record = keep
            adult.save(update_fields=["student_record"])
        else:
            adult.student_record = None
            adult.save(update_fields=["student_record"])


def _carry_over_missing_student_fields(keep, source):
    """Copy fields that only ``source`` has onto ``keep``.

    Returns True if ``keep`` was modified. Choice fields with model defaults
    ("cell" for phone_type, "PA" for state) look filled even when they were
    never actually chosen, so they are only treated as missing when the value
    they describe (phone number / address) is missing too.
    """
    import datetime

    keep_had_phone = bool(keep.phone_number and str(keep.phone_number).strip())
    keep_had_address = bool(
        (keep.address and str(keep.address).strip())
        or (keep.city and str(keep.city).strip())
    )
    keep_had_dob = bool(
        keep.date_of_birth and keep.date_of_birth != datetime.date(1900, 1, 1)
    )
    source_has_dob = bool(
        source.date_of_birth and source.date_of_birth != datetime.date(1900, 1, 1)
    )

    changed = False

    # 1. Photo
    if not bool(keep.photo) and bool(source.photo):
        keep.photo = source.photo
        changed = True

    # 2. Date of birth
    if not keep_had_dob and source_has_dob:
        keep.date_of_birth = source.date_of_birth
        changed = True

    # 3. Defaulted choice fields
    if (
        not keep_had_phone
        and source.phone_number
        and str(source.phone_number).strip()
        and source.phone_type
    ):
        keep.phone_type = source.phone_type
        changed = True

    if (
        not keep_had_address
        and (
            (source.address and str(source.address).strip())
            or (source.city and str(source.city).strip())
        )
        and source.state
    ):
        keep.state = source.state
        changed = True

    # 4. Foreign keys
    if keep.school_id is None and source.school_id is not None:
        keep.school = source.school
        changed = True

    if keep.andrew_id_sponsor_id is None and source.andrew_id_sponsor_id is not None:
        keep.andrew_id_sponsor = source.andrew_id_sponsor
        changed = True

    # 5. Many-to-many: Race/Ethnicities
    for re in source.race_ethnicities.all():
        if not keep.race_ethnicities.filter(pk=re.pk).exists():
            keep.race_ethnicities.add(re)

    # 6. Generic model fields (scalars, strings, dates, encrypted fields, booleans)
    special_fields = {
        "id",
        "user",
        "photo",
        "date_of_birth",
        "phone_type",
        "state",
        "school",
        "andrew_id_sponsor",
        "primary_contact_relationship",
        "secondary_contact_relationship",
        "created_at",
        "updated_at",
    }

    for field in Student._meta.fields:
        field_name = field.name
        if field_name in special_fields:
            continue

        keep_val = getattr(keep, field_name, None)
        source_val = getattr(source, field_name, None)

        if isinstance(field, models.BooleanField):
            # Promote True flags (e.g. can_receive_texts, on_discord, first_has_account, graduated)
            if not keep_val and source_val:
                setattr(keep, field_name, True)
                changed = True
        else:
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


def _transfer_student_user_account(keep, source):
    """If ``keep`` has no linked user but ``source`` does, transfer it.

    Returns True if ``keep.user`` was updated.
    """
    if keep.user_id or not source.user_id:
        return False
    source_user = source.user
    source.user = None
    source.save(update_fields=["user"])
    keep.user = source_user
    return True


class StudentMergeView(LoginRequiredMixin, LeadMentorRequiredMixin, FormView):
    template_name = "students/merge.html"
    form_class = StudentMergeForm
    success_url = reverse_lazy("student_list")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["students"] = list(
            Student.objects.all()
            .select_related(
                "school",
                "primary_contact_relationship__adult",
                "secondary_contact_relationship__adult",
            )
            .prefetch_related(
                "adults",
                "enrollment_set__program",
                "adultstudentrelationship_set__adult",
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

        with transaction.atomic():
            changed = _transfer_student_relationships(keep, source)
            _transfer_student_related_records(keep, source)
            changed = _carry_over_missing_student_fields(keep, source) or changed
            changed = _transfer_student_user_account(keep, source) or changed
            if changed:
                keep.save()

            # Clear unique fields before deleting source
            source.personal_email = None
            source.andrew_email = None
            source.delete()

        log_event(
            request=self.request,
            event=AuditEvent.RECORDS_MERGED,
            resource=keep,
            notes=(
                f'Student "{source.display_name}" (pk={source.pk}) merged into '
                f'"{keep.display_name}" (pk={keep.pk}). All enrollments, records, and relationships '
                f"were transferred."
            ),
        )

        messages.success(
            self.request,
            f'Merged "{source.display_name}" into "{keep.display_name}". '
            f"All enrollments, records, and relationships were transferred.",
        )
        return super().form_valid(form)
