from django.contrib import messages
from django.contrib.auth.mixins import (
    LoginRequiredMixin,
    PermissionRequiredMixin,
    UserPassesTestMixin,
)
from django.shortcuts import get_object_or_404, redirect, render

from .permission_views import can_user_read, can_user_write


class DynamicPermissionMixin(UserPassesTestMixin):
    section = None
    permission_type = "read"  # 'read' or 'write'

    def test_func(self):
        if not self.section:
            return True
        if self.permission_type == "write":
            return can_user_write(
                self.request.user, self.section, getattr(self, "object", None)
            )
        return can_user_read(self.request.user, self.section)

    def handle_no_permission(self):
        messages.error(
            self.request, "You do not have permission to access this section."
        )
        return redirect("home")


class DynamicReadPermissionMixin(DynamicPermissionMixin):
    permission_type = "read"


class DynamicWritePermissionMixin(DynamicPermissionMixin):
    permission_type = "write"


import logging
from decimal import ROUND_HALF_DOWN, Decimal

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection
from django.db.models.functions import Coalesce, Lower
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.html import strip_tags
from django.views.generic import CreateView, DetailView, ListView, UpdateView, View
from premailer import transform

logger = logging.getLogger("programs.email")
forms_logger = logging.getLogger("programs.forms")


def compute_sliding_discount_rounded(total_fees: Decimal, percent: Decimal) -> Decimal:
    """Compute sliding-scale discount as a positive Decimal rounded to the nearest dollar.

    The discount is percent of total_fees, then rounded to whole dollars using half-down rounding
    (exactly .50 rounds down; above .50 rounds up; below .50 rounds down). If inputs are missing, returns 0.
    """
    if total_fees is None or percent is None:
        return Decimal("0")
    try:
        amount = (total_fees * percent) / Decimal("100")
    except Exception:
        return Decimal("0")
    # Round to the nearest whole dollar (e.g., 12.49 -> 12, 12.50 -> 12)
    return amount.quantize(Decimal("1."), rounding=ROUND_HALF_DOWN)


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

        response = super().form_valid(form)

        obj_id = getattr(
            getattr(self, "object", None), "pk", getattr(form.instance, "pk", None)
        )
        action = "create" if is_create else "update"
        if changes:
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


from .forms import (
    AddExistingStudentToProgramForm,
    AdultForm,
    FeeAssignmentEditForm,
    FeeForm,
    ParentForm,
    PaymentForm,
    ProgramApplySelectForm,
    ProgramEmailBalancesForm,
    ProgramEmailForm,
    ProgramForm,
    QuickCreateStudentForm,
    SchoolForm,
    SlidingScaleForm,
    StudentApplicationForm,
    StudentForm,
)
from .models import (
    RELATIONSHIP_CHOICES,
    Adult,
    Enrollment,
    Fee,
    Payment,
    Program,
    RaceEthnicity,
    School,
    SlidingScale,
    Student,
    StudentApplication,
)


class ProgramListView(LoginRequiredMixin, DynamicReadPermissionMixin, ListView):
    model = Program
    template_name = "home.html"  # landing page
    context_object_name = "programs"
    section = "programs"

    def get_queryset(self):
        # Keep a base queryset; ordering will be handled in context via grouping
        return Program.objects.all()

    def get_context_data(self, **kwargs):
        from operator import attrgetter

        from django.utils import timezone

        ctx = super().get_context_data(**kwargs)
        from .permission_views import get_user_role

        ctx["role"] = get_user_role(self.request.user)
        today = timezone.localdate()
        programs = list(ctx["programs"])

        def status(prog):
            sd = prog.start_date
            ed = prog.end_date
            if sd and sd > today:
                return "future"
            if ed and ed < today:
                return "past"
            # If only start or only end or none: treat as current if not clearly future/past
            return "current"

        def sort_key(prog):
            # Sort by start_date (None last), then by name
            sd = prog.start_date
            # Use a tuple where None sorts after real dates
            return (sd is None, sd or today, prog.name or "")

        future = sorted([p for p in programs if status(p) == "future"], key=sort_key)
        current = sorted([p for p in programs if status(p) == "current"], key=sort_key)
        past = sorted([p for p in programs if status(p) == "past"], key=sort_key)

        ctx.update(
            {
                "future_programs": future,
                "current_programs": current,
                "past_programs": past,
            }
        )
        return ctx


class StudentListView(LoginRequiredMixin, DynamicReadPermissionMixin, ListView):
    model = Student
    template_name = "students/list.html"
    context_object_name = "students"
    section = "student_info"

    def get_queryset(self):
        qs = super().get_queryset()
        from .permission_views import get_user_role

        role = get_user_role(self.request.user)
        if role == "Parent":
            try:
                adult = self.request.user.adult_profile
                qs = adult.students.all()
            except (Adult.DoesNotExist, AttributeError):
                qs = Student.objects.none()

        # Order by preferred/display name if present, otherwise legal first name, then last name (case-insensitive)
        return qs.annotate(
            sort_first=Coalesce("first_name", "legal_first_name"),
        ).order_by(Lower("sort_first"), Lower("last_name"))


class StudentPhotoListView(LoginRequiredMixin, ListView):
    model = Student
    template_name = "students/photo_grid.html"
    context_object_name = "students"
    paginate_by = 48

    def get_queryset(self):
        qs = super().get_queryset()
        # Order by preferred/display name if present, otherwise legal first name, then last name (case-insensitive)
        return qs.annotate(
            sort_first=Coalesce("first_name", "legal_first_name"),
        ).order_by(Lower("sort_first"), Lower("last_name"))


class ProgramStudentPhotoListView(LoginRequiredMixin, ListView):
    model = Student
    template_name = "students/photo_grid.html"
    context_object_name = "students"
    paginate_by = 48

    def dispatch(self, request, *args, **kwargs):
        self.program = get_object_or_404(Program, pk=kwargs.get("pk"))
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        # Students enrolled in this program
        qs = Student.objects.filter(programs=self.program)
        return qs.annotate(
            sort_first=Coalesce("first_name", "legal_first_name"),
        ).order_by(Lower("sort_first"), Lower("last_name"))

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["program"] = self.program
        return ctx


class StudentEmergencyContactsView(LoginRequiredMixin, ListView):
    model = Student
    template_name = "students/emergency_contacts.html"
    context_object_name = "students"

    def get_queryset(self):
        qs = super().get_queryset().filter(active=True)
        return (
            qs.select_related("school", "primary_contact", "secondary_contact")
            .prefetch_related("adults")
            .annotate(
                sort_first=Coalesce("first_name", "legal_first_name"),
            )
            .order_by(Lower("sort_first"), Lower("last_name"))
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        # Backwards compatibility: some templates expect 'plist'
        ctx.setdefault("plist", ctx.get("students") or ctx.get("object_list"))
        return ctx


class StudentsByGradeView(LoginRequiredMixin, ListView):
    model = Student
    template_name = "students/by_grade.html"
    context_object_name = "students"

    def get_queryset(self):
        qs = super().get_queryset().filter(active=True)
        return (
            qs.select_related("school")
            .annotate(
                sort_first=Coalesce("first_name", "legal_first_name"),
            )
            .order_by("graduation_year", Lower("sort_first"), Lower("last_name"))
        )

    def get_context_data(self, **kwargs):
        from django.utils import timezone

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
        qs = super().get_queryset().filter(active=True)
        return (
            qs.select_related("school")
            .annotate(
                sort_first=Coalesce("first_name", "legal_first_name"),
            )
            .order_by("school__name", Lower("sort_first"), Lower("last_name"))
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from .permission_views import get_user_role

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


class ParentListView(LoginRequiredMixin, ListView):
    model = Adult
    template_name = "parents/list.html"
    context_object_name = "parents"

    def get_queryset(self):
        # Prefetch related students to avoid N+1 queries and order by name
        return (
            Adult.objects.all()
            .prefetch_related("students")
            .order_by("first_name", "last_name")
        )


class MentorListView(LoginRequiredMixin, ListView):
    model = Adult
    template_name = "mentors/list.html"
    context_object_name = "mentors"

    def get_queryset(self):
        return Adult.objects.filter(is_mentor=True).order_by("last_name", "first_name")


class AlumniListView(LoginRequiredMixin, ListView):
    model = Adult
    template_name = "alumni/list.html"
    context_object_name = "alumni"

    def get_queryset(self):
        # List Adults flagged as alumni
        return Adult.objects.filter(is_alumni=True).order_by("last_name", "first_name")


class StudentConvertToAlumniView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "programs.change_student"

    def post(self, request, pk):
        from django.contrib import messages
        from django.shortcuts import get_object_or_404, redirect

        student = get_object_or_404(Student, pk=pk)

        # Find or create an Adult flagged as alumni from this student's info
        def find_matching_adult(s: Student):
            emails = [s.personal_email, s.andrew_email]
            for e in emails:
                if e:
                    a = Adult.objects.filter(alumni_email__iexact=e).first()
                    if a:
                        return a
                    a = Adult.objects.filter(email__iexact=e, is_alumni=True).first()
                    if a:
                        return a
            first = (s.first_name or s.legal_first_name or "").strip()
            last = (s.last_name or "").strip()
            if first and last:
                return Adult.objects.filter(
                    first_name__iexact=first, last_name__iexact=last, is_alumni=True
                ).first()
            return None

        adult = find_matching_adult(student)
        created = False
        if not adult:
            adult = Adult.objects.create(
                first_name=student.first_name or student.legal_first_name or "",
                last_name=student.last_name or "",
                alumni_email=student.personal_email or student.andrew_email,
                is_alumni=True,
            )
            created = True
        else:
            changed = False
            if not adult.is_alumni:
                adult.is_alumni = True
                changed = True
            if not adult.alumni_email and (
                student.personal_email or student.andrew_email
            ):
                adult.alumni_email = student.personal_email or student.andrew_email
                changed = True
            if changed:
                adult.save(update_fields=["is_alumni", "alumni_email", "updated_at"])
        # Mark student as graduated (do not change active)
        if not student.graduated:
            student.graduated = True
            student.save(update_fields=["graduated", "updated_at"])
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
        # Redirect back to list or provided next
        next_url = request.GET.get("next") or request.POST.get("next")
        return redirect(next_url or "student_list")


class StudentBulkConvertToAlumniView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "programs.change_student"
    template_name = "students/convert_to_alumni.html"

    def get(self, request):
        from django.shortcuts import render
        from django.utils import timezone

        year = request.GET.get("year")
        try:
            year = int(year) if year else timezone.now().year
        except ValueError:
            year = timezone.now().year
        # Default to seniors: graduation_year equals the selected year, and active
        students = (
            Student.objects.filter(graduation_year=year, active=True)
            .annotate(sort_first=Coalesce("first_name", "legal_first_name"))
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
        from django.shortcuts import redirect, render

        action = request.POST.get("action", "convert")
        ids = request.POST.getlist("student_ids")
        year = request.POST.get("year")
        if not ids:
            messages.info(request, "No students selected.")
            if year:
                return redirect(f"{reverse('student_bulk_convert_select')}?year={year}")
            return redirect("student_bulk_convert_select")

        qs = Student.objects.filter(pk__in=ids).order_by("last_name", "first_name")

        if action == "preview":
            # Build preview info without writing changes against Adults flagged as alumni
            def find_matching_adult(s: Student):
                emails = [s.personal_email, s.andrew_email]
                for e in emails:
                    if e:
                        a = Adult.objects.filter(alumni_email__iexact=e).first()
                        if a:
                            return a
                        a = Adult.objects.filter(
                            email__iexact=e, is_alumni=True
                        ).first()
                        if a:
                            return a
                first = (s.first_name or s.legal_first_name or "").strip()
                last = (s.last_name or "").strip()
                if first and last:
                    return Adult.objects.filter(
                        first_name__iexact=first, last_name__iexact=last, is_alumni=True
                    ).first()
                return None

            will_create = []
            already_alumni = []
            for s in qs:
                if find_matching_adult(s):
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

        def find_matching_adult(s: Student):
            emails = [s.personal_email, s.andrew_email]
            for e in emails:
                if e:
                    a = Adult.objects.filter(alumni_email__iexact=e).first()
                    if a:
                        return a
                    a = Adult.objects.filter(email__iexact=e, is_alumni=True).first()
                    if a:
                        return a
            first = (s.first_name or s.legal_first_name or "").strip()
            last = (s.last_name or "").strip()
            if first and last:
                return Adult.objects.filter(
                    first_name__iexact=first, last_name__iexact=last, is_alumni=True
                ).first()
            return None

        for student in qs:
            adult = find_matching_adult(student)
            if not adult:
                Adult.objects.create(
                    first_name=student.first_name or student.legal_first_name or "",
                    last_name=student.last_name or "",
                    alumni_email=student.personal_email or student.andrew_email,
                    is_alumni=True,
                )
                created += 1
            else:
                changed = False
                if not adult.is_alumni:
                    adult.is_alumni = True
                    changed = True
                if not adult.alumni_email and (
                    student.personal_email or student.andrew_email
                ):
                    adult.alumni_email = student.personal_email or student.andrew_email
                    changed = True
                if changed:
                    adult.save(
                        update_fields=["is_alumni", "alumni_email", "updated_at"]
                    )
                existed += 1
            if not student.graduated:
                student.graduated = True
                student.save(update_fields=["graduated", "updated_at"])
                marked_graduated += 1
        messages.success(
            request,
            f"Converted {created} new alumni (Adults), {existed} already existed/updated. Marked {marked_graduated} student(s) as graduated.",
        )
        return redirect("alumni_list")


class ImportDashboardView(LoginRequiredMixin, View):
    def get(self, request):
        from django.shortcuts import render

        from .models import Program

        programs = Program.objects.all().order_by("name")
        programs_with_attendance = [p for p in programs if p.has_feature("attendance")]
        return render(
            request,
            "imports/dashboard.html",
            {
                "programs": programs,
                "attendance_programs": programs_with_attendance,
            },
        )


class StudentImportView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "programs.add_student"

    def post(self, request):
        file = request.FILES.get("file")
        if not file:
            messages.error(request, "No file uploaded.")
            return redirect("import_dashboard")
        name = file.name.lower()
        created = 0
        updated = 0
        errors = 0
        try:
            if name.endswith(".csv"):
                import csv
                import io

                text = io.TextIOWrapper(file.file, encoding="utf-8")
                reader = csv.DictReader(text)
                rows = list(reader)
            elif name.endswith(".xlsx"):
                from openpyxl import load_workbook

                wb = load_workbook(filename=file, read_only=True, data_only=True)
                ws = wb.active
                headers = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
                rows = []
                for r in ws.iter_rows(min_row=2, values_only=True):
                    rows.append({headers[i]: r[i] for i in range(len(headers))})
            else:
                messages.error(
                    request, "Unsupported file type. Please upload CSV or XLSX."
                )
                return redirect("import_dashboard")

            # Helpers
            from datetime import date, datetime

            def raw(d, *keys):
                for k in keys:
                    if k in d and d[k] is not None:
                        return d[k]
                return None

            def val(d, *keys):
                for k in keys:
                    if k in d and d[k] is not None:
                        v = str(d[k]).strip()
                        if v != "" and v.lower() != "none":
                            return v
                return None

            def val_bool(d, *keys):
                v = val(d, *keys)
                if v is None:
                    return None
                s = v.strip().lower()
                if s in ("y", "yes", "true", "t", "1"):
                    return True
                if s in ("n", "no", "false", "f", "0"):
                    return False
                return None

            def val_date(d, *keys):
                # Accept date objects from XLSX or parse common string formats
                rv = raw(d, *keys)
                if isinstance(rv, datetime):
                    return rv.date()
                if isinstance(rv, date):
                    return rv
                v = val(d, *keys)
                if not v:
                    return None
                for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
                    try:
                        return datetime.strptime(v, fmt).date()
                    except ValueError:
                        continue
                return None

            def get_or_create_parent(first, last, email):
                # Try to find by email first
                if email:
                    p = Adult.objects.filter(email__iexact=email).first()
                    if p:
                        changed_parent = False
                        if first and p.first_name != first:
                            p.first_name = first
                            changed_parent = True
                        if last and p.last_name != last:
                            p.last_name = last
                            changed_parent = True
                        if changed_parent:
                            p.save()
                        return p
                # Next try by name match
                if first and last:
                    p = Adult.objects.filter(
                        first_name__iexact=first, last_name__iexact=last
                    ).first()
                    if p:
                        if email and (p.email or "").lower() != (email or "").lower():
                            p.email = email
                            p.save()
                        return p
                # If we have at least one of name or email, create
                if first or last or email:
                    return Adult.objects.create(
                        first_name=first
                        or (email.split("@")[0] if email else "Parent"),
                        last_name=last or "(contact)",
                        email=email or None,
                        is_parent=True,
                    )
                return None

            for d in rows:
                first = val(d, "first_name", "First Name", "Preferred First Name")
                legal_first = val(d, "legal_first_name", "Legal First Name") or first
                last = val(d, "last_name", "Last Name")
                if not last or not legal_first:
                    errors += 1
                    continue

                # Simple strings
                pronouns = val(d, "pronouns", "Pronouns")
                address = val(d, "address", "Address", "Street Address")
                city = val(d, "city", "City")
                state = val(d, "state", "State")
                zip_code = val(d, "zip_code", "Zip Code", "ZIP", "Zip")
                cell_phone = val(
                    d,
                    "cell_phone_number",
                    "Cell Phone Number",
                    "Cell Phone",
                    "Phone",
                    "Phone Number",
                )
                personal_email = val(d, "personal_email", "Email", "Personal Email")
                andrew_id = val(d, "andrew_id", "Andrew ID", "AndrewID")
                andrew_email = val(d, "andrew_email", "Andrew Email")
                race_ethnicity = val(
                    d, "race_ethnicity", "Race/Ethnicity", "Race", "Ethnicity"
                )
                tshirt_size = val(d, "tshirt_size", "T-Shirt Size", "Shirt Size")
                discord_handle = val(
                    d, "discord_handle", "Discord Handle", "Discord", "Discord Username"
                )

                # Dates and booleans
                dob = val_date(d, "date_of_birth", "Date of Birth", "DOB", "Birthdate")
                seen_once = val_bool(d, "seen_once", "Seen Once")
                on_discord = val_bool(d, "on_discord", "On Discord")
                active = val_bool(d, "active", "Active")

                # School/year
                school_name = val(d, "school", "School")
                grad = val(d, "graduation_year", "Graduation Year")
                school = None
                if school_name:
                    school, _ = School.objects.get_or_create(name=school_name)
                grad_year = None
                if grad and str(grad).isdigit():
                    grad_year = int(str(grad))

                obj, created_flag = Student.objects.get_or_create(
                    last_name=last,
                    legal_first_name=legal_first,
                    defaults={
                        "first_name": first if first != legal_first else None,
                        "pronouns": pronouns,
                        "date_of_birth": dob,
                        "address": address,
                        "city": city,
                        "state": state,
                        "zip_code": zip_code,
                        "cell_phone_number": cell_phone,
                        "personal_email": personal_email,
                        "andrew_id": andrew_id,
                        "andrew_email": andrew_email,
                        "tshirt_size": tshirt_size,
                        "seen_once": seen_once if seen_once is not None else False,
                        "on_discord": on_discord if on_discord is not None else False,
                        "discord_handle": discord_handle,
                        "school": school,
                        "graduation_year": grad_year,
                        "active": active if active is not None else True,
                    },
                )
                if created_flag:
                    created += 1
                else:
                    changed = False
                    # Strings and relations
                    for field, value in [
                        ("first_name", first),
                        ("pronouns", pronouns),
                        ("address", address),
                        ("city", city),
                        ("state", state),
                        ("zip_code", zip_code),
                        ("cell_phone_number", cell_phone),
                        ("personal_email", personal_email),
                        ("andrew_id", andrew_id),
                        ("andrew_email", andrew_email),
                        ("tshirt_size", tshirt_size),
                        ("discord_handle", discord_handle),
                    ]:
                        if value and getattr(obj, field) != value:
                            setattr(obj, field, value)
                            changed = True
                    if dob and obj.date_of_birth != dob:
                        obj.date_of_birth = dob
                        changed = True
                    if school and obj.school != school:
                        obj.school = school
                        changed = True
                    if grad_year and obj.graduation_year != grad_year:
                        obj.graduation_year = grad_year
                        changed = True
                    # Booleans (allow False updates)
                    if seen_once is not None and obj.seen_once != seen_once:
                        obj.seen_once = seen_once
                        changed = True
                    if on_discord is not None and obj.on_discord != on_discord:
                        obj.on_discord = on_discord
                        changed = True
                    if active is not None and obj.active != active:
                        obj.active = active
                        changed = True
                    if changed:
                        obj.save()
                        updated += 1

                # Map race/ethnicity text to multi-select options
                try:
                    opts = RaceEthnicity.match_from_text(race_ethnicity)
                    if opts.exists():
                        obj.race_ethnicities.set(list(opts))
                except Exception:
                    logger.debug(
                        "Race/Ethnicity matching failed during import", exc_info=True
                    )

                # Parent linkage (primary and secondary)
                prim_first = val(
                    d,
                    "primary_parent_first_name",
                    "Primary Parent First Name",
                    "Primary First Name",
                    "Primary First",
                )
                prim_last = val(
                    d,
                    "primary_parent_last_name",
                    "Primary Parent Last Name",
                    "Primary Last Name",
                    "Primary Last",
                )
                prim_email = val(
                    d,
                    "primary_parent_email",
                    "Primary Parent Email",
                    "Primary Email",
                    "Primary E-mail",
                    "Primary Email Address",
                )
                sec_first = val(
                    d,
                    "secondary_parent_first_name",
                    "Secondary Parent First Name",
                    "Secondary First Name",
                    "Secondary First",
                )
                sec_last = val(
                    d,
                    "secondary_parent_last_name",
                    "Secondary Parent Last Name",
                    "Secondary Last Name",
                    "Secondary Last",
                )
                sec_email = val(
                    d,
                    "secondary_parent_email",
                    "Secondary Parent Email",
                    "Secondary Email",
                    "Secondary E-mail",
                    "Secondary Email Address",
                )

                contact_changed = False
                primary = get_or_create_parent(prim_first, prim_last, prim_email)
                secondary = get_or_create_parent(sec_first, sec_last, sec_email)
                if primary:
                    if obj.primary_contact_id != getattr(primary, "id", None):
                        obj.primary_contact = primary
                        contact_changed = True
                    # Ensure M2M link exists (both sides)
                    if primary.id and not obj.adults.filter(id=primary.id).exists():
                        obj.adults.add(primary)
                        primary.students.add(obj)
                if secondary:
                    if obj.secondary_contact_id != getattr(secondary, "id", None):
                        obj.secondary_contact = secondary
                        contact_changed = True
                    if secondary.id and not obj.adults.filter(id=secondary.id).exists():
                        obj.adults.add(secondary)
                        secondary.students.add(obj)
                if contact_changed:
                    obj.save(
                        update_fields=[
                            "primary_contact",
                            "secondary_contact",
                            "updated_at",
                        ]
                    )
                    if not created_flag:
                        # Only count as updated when not newly created and not already counted
                        updated += 1
            if created or updated:
                messages.success(
                    request,
                    f"Imported {created} new, updated {updated}. Skipped {errors}.",
                )
            else:
                messages.info(request, "No rows imported.")
        except Exception as e:
            messages.error(request, f"Import failed: {e}")
        return redirect("import_dashboard")


class ParentImportView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "programs.add_adult"

    def post(self, request):
        file = request.FILES.get("file")
        if not file:
            messages.error(request, "No file uploaded.")
            return redirect("import_dashboard")
        name = file.name.lower()
        created = 0
        updated = 0
        errors = 0
        try:
            if name.endswith(".csv"):
                import csv
                import io

                text = io.TextIOWrapper(file.file, encoding="utf-8")
                reader = csv.DictReader(text)
                rows = list(reader)
            elif name.endswith(".xlsx"):
                from openpyxl import load_workbook

                wb = load_workbook(filename=file, read_only=True)
                ws = wb.active
                headers = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
                rows = []
                for r in ws.iter_rows(min_row=2, values_only=True):
                    rows.append({headers[i]: r[i] for i in range(len(headers))})
            else:
                messages.error(
                    request, "Unsupported file type. Please upload CSV or XLSX."
                )
                return redirect("import_dashboard")

            def val(d, *keys):
                for k in keys:
                    if k in d and d[k] is not None:
                        v = str(d[k]).strip()
                        if v != "" and v.lower() != "none":
                            return v
                return None

            for d in rows:
                first = val(d, "first_name", "First Name")
                last = val(d, "last_name", "Last Name")
                if not first or not last:
                    errors += 1
                    continue
                email = val(d, "email", "Email")
                phone = val(d, "phone_number", "Phone", "Phone Number")
                obj, created_flag = Adult.objects.get_or_create(
                    first_name=first,
                    last_name=last,
                    defaults={"email": email, "phone_number": phone},
                )
                if created_flag:
                    created += 1
                else:
                    changed = False
                    if email and obj.email != email:
                        obj.email = email
                        changed = True
                    if phone and obj.phone_number != phone:
                        obj.phone_number = phone
                        changed = True
                    if changed:
                        obj.save()
                        updated += 1
            messages.success(
                request, f"Imported {created} new, updated {updated}. Skipped {errors}."
            )
        except Exception as e:
            messages.error(request, f"Import failed: {e}")
        return redirect("import_dashboard")


class RelationshipImportView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """
    Re-link existing Students to Parent/Adult records and set relationship types.
    Safe to run multiple times (idempotent). Optionally supports dry-run.
    """

    permission_required = "programs.change_student"

    def post(self, request):
        file = request.FILES.get("file")
        if not file:
            messages.error(request, "No file uploaded.")
            return redirect("import_dashboard")
        name = file.name.lower()
        dry_run = request.POST.get("dry_run") in ("1", "on", "true", "True")
        can_create_parents = request.user.has_perm("programs.add_adult")

        linked = 0
        set_primary = 0
        set_secondary = 0
        rel_updated = 0
        created_parents = 0
        would_create_parents = 0
        missing_or_ambiguous_students = 0
        skipped = 0

        try:
            # Parse CSV/XLSX similar to other imports
            if name.endswith(".csv"):
                import csv
                import io

                text = io.TextIOWrapper(file.file, encoding="utf-8")
                reader = csv.DictReader(text)
                rows = list(reader)
            elif name.endswith(".xlsx"):
                from openpyxl import load_workbook

                wb = load_workbook(filename=file, read_only=True, data_only=True)
                ws = wb.active
                headers = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
                rows = []
                for r in ws.iter_rows(min_row=2, values_only=True):
                    rows.append({headers[i]: r[i] for i in range(len(headers))})
            else:
                messages.error(
                    request, "Unsupported file type. Please upload CSV or XLSX."
                )
                return redirect("import_dashboard")

            # Helpers
            from datetime import date, datetime

            def raw(d, *keys):
                for k in keys:
                    if k in d and d[k] is not None:
                        return d[k]
                return None

            def val(d, *keys):
                for k in keys:
                    if k in d and d[k] is not None:
                        v = str(d[k]).strip()
                        if v != "" and v.lower() != "none":
                            return v
                return None

            def val_date(d, *keys):
                rv = raw(d, *keys)
                if isinstance(rv, datetime):
                    return rv.date()
                if isinstance(rv, date):
                    return rv
                s = val(d, *keys)
                if not s:
                    return None
                for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
                    try:
                        return datetime.strptime(s, fmt).date()
                    except ValueError:
                        continue
                return None

            def normalize_rel(s):
                if not s:
                    return None
                s2 = s.strip().lower()
                # Accept either key or display label
                keys = {k for k, _ in RELATIONSHIP_CHOICES}
                labels = {lbl.lower(): k for k, lbl in RELATIONSHIP_CHOICES}
                synonyms = {
                    "mom": "mother",
                    "dad": "father",
                    "grandma": "grandmother",
                    "grandpa": "grandfather",
                    "guardian": "guardian",
                    "parent": "parent",
                }
                if s2 in keys:
                    return s2
                if s2 in labels:
                    return labels[s2]
                if s2 in synonyms and synonyms[s2] in keys:
                    return synonyms[s2]
                return None

            def resolve_student(d):
                # Priority: ID -> Andrew ID -> (First/Legal First + Last + DOB) -> (First/Legal First + Last)
                sid = val(d, "student_id", "Student ID", "ID")
                if sid and str(sid).isdigit():
                    st = Student.objects.filter(pk=int(str(sid))).first()
                    if st:
                        return st

                aid = val(d, "andrew_id", "Andrew ID", "AndrewID")
                if aid:
                    st = Student.objects.filter(andrew_id__iexact=aid).first()
                    if st:
                        return st

                last = val(d, "last_name", "Last Name")
                first = val(d, "first_name", "First Name", "Preferred First Name")
                legal_first = val(d, "legal_first_name", "Legal First Name") or first
                dob = val_date(d, "date_of_birth", "Date of Birth", "DOB", "Birthdate")

                if not last or not legal_first:
                    return None

                qs = Student.objects.filter(
                    last_name__iexact=last, legal_first_name__iexact=legal_first
                )
                if dob:
                    qs = qs.filter(date_of_birth=dob)
                count = qs.count()
                if count == 1:
                    return qs.first()
                if count == 0 and first and first != legal_first:
                    # Try match on preferred first + last (+dob)
                    qs = Student.objects.filter(
                        last_name__iexact=last, first_name__iexact=first
                    )
                    if dob:
                        qs = qs.filter(date_of_birth=dob)
                    if qs.count() == 1:
                        return qs.first()
                return None if qs.count() != 1 else qs.first()

            def find_or_create_parent(first, last, email):
                # Try resolve by email first
                p = None
                if email:
                    p = Adult.objects.filter(email__iexact=email).first()
                if not p and first and last:
                    p = Adult.objects.filter(
                        first_name__iexact=first, last_name__iexact=last
                    ).first()
                created = False
                if not p:
                    if dry_run or not can_create_parents:
                        return None, False, True  # would create
                    p = Adult.objects.create(
                        first_name=first
                        or (email.split("@")[0] if email else "Parent"),
                        last_name=last or "(contact)",
                        email=email or None,
                        is_parent=True,
                    )
                    created = True
                else:
                    # If we found existing Adult but not flagged as parent, set it
                    if not dry_run and not p.is_parent:
                        p.is_parent = True
                        p.save(update_fields=["is_parent", "updated_at"])
                return p, created, False

            for d in rows:
                student = resolve_student(d)
                if not student:
                    missing_or_ambiguous_students += 1
                    continue

                groups = [
                    {
                        "role": "primary",
                        "first": val(
                            d,
                            "primary_parent_first_name",
                            "Primary Parent First Name",
                            "Primary First Name",
                            "Primary First",
                        ),
                        "last": val(
                            d,
                            "primary_parent_last_name",
                            "Primary Parent Last Name",
                            "Primary Last Name",
                            "Primary Last",
                        ),
                        "email": val(
                            d,
                            "primary_parent_email",
                            "Primary Parent Email",
                            "Primary Email",
                        ),
                        "rel": val(
                            d,
                            "primary_parent_relationship",
                            "Primary Parent Relationship",
                            "Primary Relationship",
                        ),
                    },
                    {
                        "role": "secondary",
                        "first": val(
                            d,
                            "secondary_parent_first_name",
                            "Secondary Parent First Name",
                            "Secondary First Name",
                            "Secondary First",
                        ),
                        "last": val(
                            d,
                            "secondary_parent_last_name",
                            "Secondary Parent Last Name",
                            "Secondary Last Name",
                            "Secondary Last",
                        ),
                        "email": val(
                            d,
                            "secondary_parent_email",
                            "Secondary Parent Email",
                            "Secondary Email",
                        ),
                        "rel": val(
                            d,
                            "secondary_parent_relationship",
                            "Secondary Parent Relationship",
                            "Secondary Relationship",
                        ),
                    },
                ]

                updated_student_fields = set()

                for g in groups:
                    if not (g["first"] or g["last"] or g["email"]):
                        continue
                    adult, created_flag, would_create = find_or_create_parent(
                        g["first"], g["last"], g["email"]
                    )
                    if would_create:
                        would_create_parents += 1
                        continue
                    if created_flag:
                        created_parents += 1
                    if not adult:
                        skipped += 1
                        continue

                    # Relationship type (global per Adult)
                    rel_key = normalize_rel(g["rel"])
                    if rel_key and adult.relationship_to_student != rel_key:
                        if not dry_run:
                            adult.relationship_to_student = rel_key
                            adult.save(
                                update_fields=["relationship_to_student", "updated_at"]
                            )
                        rel_updated += 1

                    # Ensure Adult is linked to Student (M2M)
                    if adult.id and not student.adults.filter(id=adult.id).exists():
                        if not dry_run:
                            student.adults.add(adult)
                            adult.students.add(student)
                        linked += 1

                    # Optionally set primary/secondary contact
                    if g["role"] == "primary":
                        if student.primary_contact_id != adult.id:
                            if not dry_run:
                                student.primary_contact = adult
                                updated_student_fields.add("primary_contact")
                            set_primary += 1
                    elif g["role"] == "secondary":
                        if student.secondary_contact_id != adult.id:
                            if not dry_run:
                                student.secondary_contact = adult
                                updated_student_fields.add("secondary_contact")
                            set_secondary += 1

                if updated_student_fields and not dry_run:
                    fields = list(updated_student_fields) + ["updated_at"]
                    student.save(update_fields=fields)

            # Compose message
            notes = []
            if dry_run:
                notes.append("DRY RUN (no changes saved)")
            if not can_create_parents:
                notes.append(
                    "Note: lacking permission to create parents; rows requiring new parent were skipped."
                )
            extras = f" {'; '.join(notes)}" if notes else ""
            messages.success(
                request,
                f"Relationships import: linked {linked} (primary set {set_primary}, secondary set {set_secondary}); "
                f"updated relationship types {rel_updated}; "
                f"created parents {created_parents}{(' (would create: ' + str(would_create_parents) + ')' if dry_run else '')}; "
                f"missing/ambiguous students {missing_or_ambiguous_students}; skipped {skipped}.{extras}",
            )
        except Exception as e:
            messages.error(request, f"Import failed: {e}")
        return redirect("import_dashboard")


class MentorImportView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "programs.add_adult"

    def post(self, request):
        file = request.FILES.get("file")
        if not file:
            messages.error(request, "No file uploaded.")
            return redirect("import_dashboard")
        name = file.name.lower()
        created = 0
        updated = 0
        errors = 0
        try:
            if name.endswith(".csv"):
                import csv
                import io

                text = io.TextIOWrapper(file.file, encoding="utf-8")
                reader = csv.DictReader(text)
                rows = list(reader)
            elif name.endswith(".xlsx"):
                from openpyxl import load_workbook

                wb = load_workbook(filename=file, read_only=True)
                ws = wb.active
                headers = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
                rows = []
                for r in ws.iter_rows(min_row=2, values_only=True):
                    rows.append({headers[i]: r[i] for i in range(len(headers))})
            else:
                messages.error(
                    request, "Unsupported file type. Please upload CSV or XLSX."
                )
                return redirect("import_dashboard")

            def val(d, *keys):
                for k in keys:
                    if k in d and d[k] is not None:
                        v = str(d[k]).strip()
                        if v != "" and v.lower() != "none":
                            return v
                return None

            for d in rows:
                first = val(d, "first_name", "First Name")
                last = val(d, "last_name", "Last Name")
                if not first or not last:
                    errors += 1
                    continue
                email = val(d, "personal_email", "Email", "Personal Email")
                andrew_email = val(d, "andrew_email", "Andrew Email")
                role = val(d, "role", "Role") or "mentor"
                obj, created_flag = Adult.objects.get_or_create(
                    first_name=first,
                    last_name=last,
                    defaults={
                        "personal_email": email,
                        "andrew_email": andrew_email,
                        "role": role,
                        "is_mentor": True,
                    },
                )
                if created_flag:
                    created += 1
                else:
                    changed = False
                    for field, value in [
                        ("personal_email", email),
                        ("andrew_email", andrew_email),
                        ("role", role),
                    ]:
                        if value and getattr(obj, field) != value:
                            setattr(obj, field, value)
                            changed = True
                    if changed:
                        obj.save()
                        updated += 1
            messages.success(
                request, f"Imported {created} new, updated {updated}. Skipped {errors}."
            )
        except Exception as e:
            messages.error(request, f"Import failed: {e}")
        return redirect("import_dashboard")


class SchoolImportView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "programs.add_school"

    def post(self, request):
        file = request.FILES.get("file")
        if not file:
            messages.error(request, "No file uploaded.")
            return redirect("import_dashboard")
        name = file.name.lower()
        created = 0
        updated = 0
        errors = 0
        try:
            if name.endswith(".csv"):
                import csv
                import io

                text = io.TextIOWrapper(file.file, encoding="utf-8")
                reader = csv.DictReader(text)
                rows = list(reader)
            elif name.endswith(".xlsx"):
                from openpyxl import load_workbook

                wb = load_workbook(filename=file, read_only=True)
                ws = wb.active
                headers = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
                rows = []
                for r in ws.iter_rows(min_row=2, values_only=True):
                    rows.append({headers[i]: r[i] for i in range(len(headers))})
            else:
                messages.error(
                    request, "Unsupported file type. Please upload CSV or XLSX."
                )
                return redirect("import_dashboard")

            def val(d, *keys):
                for k in keys:
                    if k in d and d[k] is not None:
                        v = str(d[k]).strip()
                        if v != "" and v.lower() != "none":
                            return v
                return None

            for d in rows:
                school_name = val(d, "name", "Name", "School")
                if not school_name:
                    errors += 1
                    continue
                district = val(d, "district", "District", "School District")
                street = val(d, "street_address", "Street", "Street Address", "Address")
                city = val(d, "city", "City")
                state = val(d, "state", "State")
                zip_code = val(
                    d, "zip", "ZIP", "Zip", "zip_code", "Zip Code", "Postal Code"
                )
                obj, created_flag = School.objects.get_or_create(
                    name=school_name,
                    defaults={
                        "district": district,
                        "street_address": street,
                        "city": city,
                        "state": state,
                        "zip_code": zip_code,
                    },
                )
                if created_flag:
                    created += 1
                else:
                    changed = False
                    for field, value in [
                        ("district", district),
                        ("street_address", street),
                        ("city", city),
                        ("state", state),
                        ("zip_code", zip_code),
                    ]:
                        if value and getattr(obj, field) != value:
                            setattr(obj, field, value)
                            changed = True
                    if changed:
                        obj.save()
                        updated += 1
            messages.success(
                request, f"Imported {created} new, updated {updated}. Skipped {errors}."
            )
        except Exception as e:
            messages.error(request, f"Import failed: {e}")
        return redirect("import_dashboard")


class MentorCreateView(
    LogFormSaveMixin, LoginRequiredMixin, PermissionRequiredMixin, CreateView
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
    LogFormSaveMixin, LoginRequiredMixin, PermissionRequiredMixin, UpdateView
):
    model = Adult
    form_class = AdultForm
    template_name = "adults/form.html"
    permission_required = "programs.change_adult"

    def get_success_url(self):
        next_url = self.request.GET.get("next")
        return next_url or reverse("mentor_edit", args=[self.object.pk])


# --- Schools list/create/edit ---
class SchoolListView(LoginRequiredMixin, ListView):
    model = School
    template_name = "schools/list.html"
    context_object_name = "schools"


class SchoolCreateView(
    LogFormSaveMixin, LoginRequiredMixin, PermissionRequiredMixin, CreateView
):
    model = School
    form_class = SchoolForm
    template_name = "schools/form.html"
    permission_required = "programs.add_school"

    def get_success_url(self):
        # After creating a School, return to the Schools listing
        return reverse("school_list")


class SchoolUpdateView(
    LogFormSaveMixin, LoginRequiredMixin, PermissionRequiredMixin, UpdateView
):
    model = School
    form_class = SchoolForm
    template_name = "schools/form.html"
    permission_required = "programs.change_school"

    def get_success_url(self):
        next_url = self.request.GET.get("next")
        return next_url or reverse("school_edit", args=[self.object.pk])


class ProgramEmailView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "programs.view_program"  # basic permission to access
    template_name = "programs/email_form.html"

    def get(self, request, pk=None):
        program = get_object_or_404(Program, pk=pk) if pk else None
        form = ProgramEmailForm(program=program) if program else ProgramEmailForm()
        return self._render(form, program)

    def post(self, request, pk=None):
        program = get_object_or_404(Program, pk=pk) if pk else None
        form = (
            ProgramEmailForm(request.POST, program=program)
            if program
            else ProgramEmailForm(request.POST)
        )
        if form.is_valid():
            prog = program or form.cleaned_data["program"]
            groups = form.cleaned_data["recipient_groups"]
            subject = form.cleaned_data["subject"]
            html_body = form.cleaned_data["body"]
            # Inline CSS for better email client compatibility
            try:
                inlined_html_body = transform(html_body)
            except Exception:
                inlined_html_body = html_body
            text_body = strip_tags(inlined_html_body)
            test_email = form.cleaned_data.get("test_email")

            recipients = set()
            if "students" in groups:
                for s in Student.objects.filter(programs=prog, active=True):
                    if s.personal_email:
                        recipients.add(s.personal_email)
                    elif s.andrew_email:
                        recipients.add(s.andrew_email)
            if "parents" in groups:
                parent_emails = (
                    Adult.objects.filter(
                        students__programs=prog, email_updates=True, active=True
                    )
                    .annotate(
                        best_email=Coalesce("personal_email", "email", "andrew_email")
                    )
                    .values_list("email", flat=True)
                )
                for e in parent_emails:
                    if e:
                        recipients.add(e)
            if "mentors" in groups:
                for m in Adult.objects.filter(is_mentor=True, active=True):
                    # Prefer personal_email, then andrew_email, then email
                    if m.personal_email:
                        recipients.add(m.personal_email)
                    elif m.andrew_email:
                        recipients.add(m.andrew_email)
                    elif m.email:
                        recipients.add(m.email)

            if not recipients and not test_email:
                messages.error(request, "No recipients found for the selected groups.")
                return self._render(form, prog)

            to_send = [test_email] if test_email else sorted(recipients)

            # Determine sender account and SMTP credentials
            selected = form.cleaned_data.get("from_account")
            accounts = getattr(settings, "EMAIL_SENDER_ACCOUNTS", []) or []
            acc = None
            if accounts and selected and selected != "DEFAULT":
                # Match by key or email value
                for a in accounts:
                    key = a.get("key") or a.get("email")
                    if key == selected:
                        acc = a
                        break
            # Build SMTP connection using selected account credentials if provided
            conn_kwargs = {
                "backend": getattr(
                    settings,
                    "EMAIL_BACKEND",
                    "django.core.mail.backends.smtp.EmailBackend",
                ),
                "host": getattr(settings, "EMAIL_HOST", ""),
                "port": getattr(settings, "EMAIL_PORT", 465),
                "use_tls": getattr(settings, "EMAIL_USE_TLS", False),
                "use_ssl": getattr(settings, "EMAIL_USE_SSL", True),
                "timeout": getattr(settings, "EMAIL_TIMEOUT", 10),
            }
            if acc:
                conn_kwargs.update(
                    {
                        "username": acc.get("username") or "",
                        "password": acc.get("password") or "",
                    }
                )
                from_email = acc.get("email") or getattr(
                    settings, "DEFAULT_FROM_EMAIL", "no-reply@example.com"
                )
                # Include display_name if provided
                display_name = acc.get("display_name")
                if display_name:
                    from_email = f'"{display_name}" <{from_email}>'
            else:
                # Fall back to global credentials and default from address
                conn_kwargs.update(
                    {
                        "username": getattr(settings, "EMAIL_HOST_USER", ""),
                        "password": getattr(settings, "EMAIL_HOST_PASSWORD", ""),
                    }
                )
                from_email = getattr(
                    settings, "DEFAULT_FROM_EMAIL", "no-reply@example.com"
                )
                # Include sender name from settings if available
                sender_name = getattr(settings, "DEFAULT_FROM_NAME", None)
                if sender_name:
                    from_email = f'"{sender_name}" <{from_email}>'

            connection = get_connection(**conn_kwargs)
            # For test sends, put recipient in the To field (some SMTP providers reject emails with empty To)
            if test_email:
                email = EmailMultiAlternatives(
                    subject=subject,
                    body=text_body,
                    from_email=from_email,
                    to=[test_email],
                    connection=connection,
                )
                email.bcc = []
            else:
                email = EmailMultiAlternatives(
                    subject=subject,
                    body=text_body,
                    from_email=from_email,
                    to=[],
                    connection=connection,
                )
                email.to = []  # ensure empty
                email.bcc = to_send
            email.attach_alternative(inlined_html_body, "text/html")

            # Log details about the outgoing message
            preview_recipients = to_send[:20]
            logger.info(
                "ProgramEmail: preparing to send email | from=%s | to_count=%d | subject=%s | test=%s",
                from_email,
                len(to_send),
                subject,
                bool(test_email),
            )
            logger.debug(
                "ProgramEmail: recipient sample (first %d): %s",
                len(preview_recipients),
                preview_recipients,
            )

            try:
                sent_count = email.send(fail_silently=False)
                logger.info(
                    "ProgramEmail: email sent successfully | from=%s | to_count=%d | subject=%s | sent_count=%s",
                    from_email,
                    len(to_send),
                    subject,
                    sent_count,
                )
                messages.success(
                    request,
                    f"Email sent to {len(to_send)} recipient(s){' (test only)' if test_email else ''}.",
                )
                # Redirect back to program detail if coming from there, otherwise stay
                if pk:
                    return redirect("program_detail", pk=pk)
                return redirect("program_messaging")
            except Exception as e:
                logger.error(
                    "ProgramEmail: email send FAILED | from=%s | to_count=%d | subject=%s | error=%s",
                    from_email,
                    len(to_send),
                    subject,
                    e,
                    exc_info=True,
                )
                messages.error(request, f"Failed to send email: {e}")
                return self._render(form, prog)

        return self._render(form, program)

    def _render(self, form, program):
        from django.shortcuts import render

        ctx = {"form": form, "program": program}
        return render(self.request, self.template_name, ctx)


class ProgramDetailView(LoginRequiredMixin, DynamicReadPermissionMixin, DetailView):
    model = Program
    template_name = "programs/detail.html"
    context_object_name = "program"
    section = "programs"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from .permission_views import get_user_role

        ctx["role"] = get_user_role(self.request.user)
        program = self.object
        from .permission_views import can_user_read, can_user_write

        role = ctx["role"]

        # Prepare annotated queryset for consistent sorting
        from django.db.models.functions import Coalesce, Lower

        base_qs = (
            program.students.select_related("user")
            .all()
            .annotate(
                sort_first=Lower(Coalesce("first_name", "legal_first_name")),
                sort_last=Lower("last_name"),
            )
        )

        # Parent restriction
        if role == "Parent":
            try:
                adult = self.request.user.adult_profile
                base_qs = base_qs.filter(adults=adult)
            except (Adult.DoesNotExist, AttributeError):
                base_qs = Student.objects.none()

        # Split into active and inactive sections
        ctx["active_students"] = base_qs.filter(active=True).order_by(
            "sort_first", "sort_last"
        )
        ctx["inactive_students"] = base_qs.filter(active=False).order_by(
            "sort_first", "sort_last"
        )
        # Backwards compatibility (old templates may rely on a single list)
        ctx["enrolled_students"] = list(ctx["active_students"]) + list(
            ctx["inactive_students"]
        )

        ctx["can_manage_students"] = can_user_write(self.request.user, "student_info")
        ctx["can_add_payment"] = can_user_write(self.request.user, "payments")
        ctx["can_add_sliding_scale"] = can_user_write(self.request.user, "payments")
        ctx["can_manage_fees"] = can_user_write(self.request.user, "fees")
        ctx["can_view_payments"] = can_user_read(self.request.user, "payments")
        ctx["can_view_attendance"] = can_user_read(self.request.user, "attendance")

        if ctx["can_manage_students"]:
            ctx["add_existing_form"] = AddExistingStudentToProgramForm(program=program)
            ctx["quick_create_form"] = QuickCreateStudentForm()
        return ctx


class ProgramCreateView(LogFormSaveMixin, CreateView):
    model = Program
    form_class = ProgramForm
    template_name = "programs/form.html"

    def get_success_url(self):
        return reverse("program_detail", args=[self.object.pk])


class ProgramUpdateView(LogFormSaveMixin, UpdateView):
    model = Program
    form_class = ProgramForm
    template_name = "programs/form.html"

    def get_success_url(self):
        return reverse("program_detail", args=[self.object.pk])


# --- Student edit ---
class StudentUpdateView(
    LogFormSaveMixin,
    LoginRequiredMixin,
    PermissionRequiredMixin,
    DynamicWritePermissionMixin,
    UpdateView,
):
    model = Student
    form_class = StudentForm
    template_name = "students/form.html"
    permission_required = "programs.change_student"
    section = "student_info"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from .permission_views import get_user_role

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
        return ctx

    def form_valid(self, form):
        response = super().form_valid(form)
        # Persist relationship selections for each selected parent (note: global per Parent)
        rel_map = {
            k[len("parent_rel_") :]: v
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
                p = Adult.objects.filter(pk=pid).first()
                if p and p.relationship_to_student != rel:
                    p.relationship_to_student = rel
                    p.save(update_fields=["relationship_to_student"])
        return response

    def get_success_url(self):
        next_url = self.request.GET.get("next")
        return next_url or reverse("student_edit", args=[self.object.pk])


class StudentCreateView(
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
        from .permission_views import get_user_role

        ctx["role"] = get_user_role(self.request.user)
        ctx["RELATIONSHIP_CHOICES"] = RELATIONSHIP_CHOICES
        return ctx

    def form_valid(self, form):
        response = super().form_valid(form)
        # Persist relationship selections for each selected parent (note: global per Parent)
        rel_map = {
            k[len("parent_rel_") :]: v
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
                p = Adult.objects.filter(pk=pid).first()
                if p and p.relationship_to_student != rel:
                    p.relationship_to_student = rel
                    p.save(update_fields=["relationship_to_student"])
        return response

    def get_success_url(self):
        # After creating a Student, return to the Students listing
        return reverse("student_list")


class StudentDetailView(LoginRequiredMixin, DetailView):
    model = Student
    template_name = "students/detail.html"
    context_object_name = "student"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from .permission_views import get_user_role

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


# --- Program student management actions ---
class ProgramStudentAddView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "programs.change_student"

    def post(self, request, pk):
        program = get_object_or_404(Program, pk=pk)
        form = AddExistingStudentToProgramForm(request.POST, program=program)
        if form.is_valid():
            student = form.cleaned_data["student"]
            Enrollment.objects.get_or_create(student=student, program=program)
            messages.success(request, f"Added {student} to {program}.")
        else:
            messages.error(request, "Could not add student to program.")
        return redirect("program_detail", pk=program.pk)


class ProgramStudentQuickCreateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "programs.add_student"

    def post(self, request, pk):
        program = get_object_or_404(Program, pk=pk)
        form = QuickCreateStudentForm(request.POST)
        if form.is_valid():
            student = form.save()
            Enrollment.objects.get_or_create(student=student, program=program)
            messages.success(request, f"Created {student} and added to {program}.")
        else:
            messages.error(request, "Could not create student.")
        return redirect("program_detail", pk=program.pk)


class ProgramStudentRemoveView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "programs.change_student"

    def post(self, request, pk, student_id):
        program = get_object_or_404(Program, pk=pk)
        student = get_object_or_404(Student, pk=student_id)
        Enrollment.objects.filter(student=student, program=program).delete()
        messages.success(request, f"Removed {student} from {program}.")
        return redirect("program_detail", pk=program.pk)


# --- Parent create/edit ---
class ParentCreateView(
    LogFormSaveMixin, LoginRequiredMixin, PermissionRequiredMixin, CreateView
):
    model = Adult
    form_class = ParentForm
    template_name = "parents/form.html"
    permission_required = "programs.add_adult"

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
    LogFormSaveMixin, LoginRequiredMixin, PermissionRequiredMixin, UpdateView
):
    model = Adult
    form_class = ParentForm
    template_name = "parents/form.html"
    permission_required = "programs.change_adult"

    def get_success_url(self):
        next_url = self.request.GET.get("next")
        return next_url or reverse("parent_edit", args=[self.object.pk])


# --- Payment create ---
class ProgramPaymentCreateView(
    LogFormSaveMixin,
    LoginRequiredMixin,
    PermissionRequiredMixin,
    DynamicWritePermissionMixin,
    CreateView,
):
    model = Payment
    form_class = PaymentForm
    template_name = "programs/payment_form.html"
    permission_required = "programs.add_payment"
    section = "payments"

    def dispatch(self, request, *args, **kwargs):
        self.program = get_object_or_404(Program, pk=kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["program"] = self.program
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["program"] = self.program
        return ctx

    def form_valid(self, form):
        obj = form.save(commit=False)
        # Ensure program is set from the URL context
        obj.program = self.program
        obj.save()
        # Log creation with a concise summary and field values
        user = getattr(self.request, "user", None)
        user_repr = (
            f"{getattr(user, 'pk', 'anon')}:{getattr(user, 'username', 'anonymous')}"
            if getattr(user, "is_authenticated", False)
            else "anonymous"
        )
        forms_logger.info(
            "FormSave: Payment[%s] create by %s | student=%s | program=%s | fee=%s | amount=%s | paid_via=%s | paid_on=%s",
            obj.pk,
            user_repr,
            self._fmt_val(getattr(obj, "student", None)),
            self._fmt_val(getattr(obj, "program", None)),
            self._fmt_val(getattr(obj, "fee", None)),
            self._fmt_val(getattr(obj, "amount", None)),
            self._fmt_val(getattr(obj, "paid_via", None)),
            self._fmt_val(getattr(obj, "paid_on", None)),
        )
        messages.success(self.request, "Payment recorded successfully.")
        return redirect("program_detail", pk=self.program.pk)


class ProgramPaymentDetailView(LoginRequiredMixin, View):
    def get(self, request, pk, payment_id):
        program = get_object_or_404(Program, pk=pk)
        payment = get_object_or_404(Payment, pk=payment_id)
        # Ensure payment belongs to this program
        if payment.program_id != program.id:
            messages.error(request, "Payment does not belong to this program.")
            return redirect("program_detail", pk=program.pk)
        student = payment.student
        # Ensure enrollment
        if not Enrollment.objects.filter(student=student, program=program).exists():
            messages.error(request, f"{student} is not enrolled in {program}.")
            return redirect("program_detail", pk=program.pk)
        from django.shortcuts import render

        return render(
            request,
            "programs/payment_detail.html",
            {
                "program": program,
                "student": student,
                "payment": payment,
            },
        )


class ProgramPaymentPrintView(LoginRequiredMixin, View):
    def get(self, request, pk, payment_id):
        program = get_object_or_404(Program, pk=pk)
        payment = get_object_or_404(Payment, pk=payment_id)
        if payment.program_id != program.id:
            messages.error(request, "Payment does not belong to this program.")
            return redirect("program_detail", pk=program.pk)
        student = payment.student
        if not Enrollment.objects.filter(student=student, program=program).exists():
            messages.error(request, f"{student} is not enrolled in {program}.")
            return redirect("program_detail", pk=program.pk)
        from django.shortcuts import render

        return render(
            request,
            "programs/payment_print.html",
            {
                "program": program,
                "student": student,
                "payment": payment,
            },
        )


class ProgramSlidingScaleCreateView(
    LogFormSaveMixin,
    LoginRequiredMixin,
    PermissionRequiredMixin,
    DynamicWritePermissionMixin,
    CreateView,
):
    model = SlidingScale
    form_class = SlidingScaleForm
    template_name = "programs/sliding_scale_form.html"
    permission_required = "programs.add_slidingscale"
    section = "sliding_scale"

    def dispatch(self, request, *args, **kwargs):
        self.program = get_object_or_404(Program, pk=kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["program"] = self.program
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["program"] = self.program
        return ctx

    def form_valid(self, form):
        # If a sliding scale already exists for this student and program, redirect to edit
        student = form.cleaned_data.get("student")
        existing = (
            SlidingScale.objects.filter(student=student, program=self.program).first()
            if student
            else None
        )
        if existing:
            messages.info(
                self.request,
                "A sliding scale already exists for this student. You can edit it below.",
            )
            return redirect(
                "program_sliding_scale_edit", pk=self.program.pk, sliding_id=existing.pk
            )
        # Otherwise, create normally; guard against race condition
        obj = form.save(commit=False)
        obj.program = self.program
        try:
            obj.save()
        except Exception:
            # In case of a late conflict (e.g., unique_together race), redirect to edit
            existing = SlidingScale.objects.filter(
                student=obj.student, program=self.program
            ).first()
            if existing:
                messages.info(
                    self.request,
                    "A sliding scale already exists for this student. You can edit it below.",
                )
                return redirect(
                    "program_sliding_scale_edit",
                    pk=self.program.pk,
                    sliding_id=existing.pk,
                )
            raise
        # Log creation
        user = getattr(self.request, "user", None)
        user_repr = (
            f"{getattr(user, 'pk', 'anon')}:{getattr(user, 'username', 'anonymous')}"
            if getattr(user, "is_authenticated", False)
            else "anonymous"
        )
        forms_logger.info(
            "FormSave: SlidingScale[%s] create by %s | student=%s | program=%s | percent=%s",
            obj.pk,
            user_repr,
            self._fmt_val(getattr(obj, "student", None)),
            self._fmt_val(getattr(self, "program", None)),
            self._fmt_val(getattr(obj, "percent", None)),
        )
        messages.success(self.request, "Sliding scale saved successfully.")
        return redirect("program_detail", pk=self.program.pk)


class ProgramSlidingScaleUpdateView(
    LogFormSaveMixin,
    LoginRequiredMixin,
    PermissionRequiredMixin,
    DynamicWritePermissionMixin,
    UpdateView,
):
    model = SlidingScale
    form_class = SlidingScaleForm
    template_name = "programs/sliding_scale_form.html"
    permission_required = "programs.change_slidingscale"
    section = "sliding_scale"

    def dispatch(self, request, *args, **kwargs):
        self.program = get_object_or_404(Program, pk=kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_object(self, queryset=None):
        return get_object_or_404(
            SlidingScale, pk=self.kwargs["sliding_id"], program=self.program
        )

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["program"] = self.program
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["program"] = self.program
        return ctx

    def form_valid(self, form):
        obj = form.save(commit=False)
        obj.program = self.program
        # Capture old values for changed fields before saving
        try:
            before = SlidingScale.objects.get(pk=obj.pk)
        except SlidingScale.DoesNotExist:
            before = None
        obj.save()
        # Log update with field-level changes when possible
        user = getattr(self.request, "user", None)
        user_repr = (
            f"{getattr(user, 'pk', 'anon')}:{getattr(user, 'username', 'anonymous')}"
            if getattr(user, "is_authenticated", False)
            else "anonymous"
        )
        for f in getattr(form, "changed_data", []) or []:
            old = getattr(before, f, None) if before is not None else None
            new = getattr(obj, f, None)
            forms_logger.info(
                "FormSave: %s[%s] %s by %s | field=%s | from=%s | to=%s",
                "SlidingScale",
                obj.pk,
                "update",
                user_repr,
                f,
                self._fmt_val(old),
                self._fmt_val(new),
            )
        messages.success(self.request, "Sliding scale updated successfully.")
        return redirect("program_detail", pk=self.program.pk)


class ProgramStudentBalanceView(LoginRequiredMixin, DynamicReadPermissionMixin, View):
    section = "payments"

    def get(self, request, pk, student_id):
        program = get_object_or_404(Program, pk=pk)
        student = get_object_or_404(Student, pk=student_id)

        # Object level check for Parents
        from .permission_views import get_user_role

        if get_user_role(request.user) == "Parent":
            try:
                adult = request.user.adult_profile
                if student not in adult.students.all():
                    messages.error(
                        request,
                        "You do not have permission to view this balance sheet.",
                    )
                    return redirect("home")
            except Exception:
                messages.error(
                    request, "You do not have permission to view this balance sheet."
                )
                return redirect("home")
        # Ensure enrollment
        if not Enrollment.objects.filter(student=student, program=program).exists():
            messages.error(request, f"{student} is not enrolled in {program}.")
            return redirect("program_detail", pk=program.pk)

        # Gather entries: fees (program), sliding scale (if exists), and payments (student for program's fees)
        entries = []
        # Fees: positive amounts
        # Use the editable fee.date when provided; otherwise fall back to created_at
        fees = Fee.objects.filter(program=program)
        for fee in fees:
            # If this fee has explicit assignments, include only if this student is assigned
            if (
                fee.assignments.exists()
                and not fee.assignments.filter(student=student).exists()
            ):
                continue
            fee_date = fee.date or (fee.created_at.date() if fee.created_at else None)
            entries.append(
                {
                    "date": fee_date,
                    "type": "Fee",
                    "name": fee.name,
                    "amount": fee.amount,
                }
            )
        # Sliding scale: negative amount (discount), include if exists and user has permission
        from .permission_views import can_user_read

        can_view_sliding = can_user_read(request.user, "sliding_scale")
        sliding = SlidingScale.objects.filter(student=student, program=program).first()
        # Compute total fees first to apply percent-based discount
        from decimal import Decimal

        total_fees_for_discount = sum(
            [fee.amount for fee in Fee.objects.filter(program=program)],
            start=Decimal("0"),
        )
        if sliding and sliding.percent is not None and can_view_sliding:
            discount = compute_sliding_discount_rounded(
                total_fees_for_discount, sliding.percent
            )
            entries.append(
                {
                    "date": sliding.created_at.date(),
                    "type": "Sliding Scale",
                    "name": f"Sliding scale ({sliding.percent}%)",
                    "amount": -discount,
                }
            )
        # Payments: negative amounts
        payments = Payment.objects.filter(student=student, program=program)
        for p in payments:
            via = dict(Payment.PAID_VIA_CHOICES).get(p.paid_via, p.paid_via)
            details = (
                f" (check #{p.check_number})"
                if (p.paid_via == "check" and p.check_number)
                else ""
            )
            if p.paid_via == "other" and p.notes:
                details += f" — {p.notes}"
            entries.append(
                {
                    "date": p.paid_on,
                    "type": "Payment",
                    "name": f"Payment via {via}{details}",
                    "amount": -p.amount,
                    "payment_id": p.id,
                }
            )

        # Sort by date (editable fee date, sliding scale created_at, payment paid_on)
        # Ensure None dates sort last
        entries.sort(key=lambda e: (e["date"] is None, e["date"], e["type"]))

        # Totals and balance
        total_fees = sum([e["amount"] for e in entries if e["type"] == "Fee"])
        total_sliding = -sum(
            [e["amount"] for e in entries if e["type"] == "Sliding Scale"]
        )  # positive figure
        total_payments = -sum(
            [e["amount"] for e in entries if e["type"] == "Payment"]
        )  # positive figure
        balance = total_fees - total_sliding - total_payments

        from django.shortcuts import render

        return render(
            request,
            "programs/balance_sheet.html",
            {
                "program": program,
                "student": student,
                "entries": entries,
                "total_fees": total_fees,
                "total_sliding": total_sliding,
                "total_payments": total_payments,
                "balance": balance,
            },
        )


class ProgramStudentBalancePrintView(
    LoginRequiredMixin, DynamicReadPermissionMixin, View
):
    section = "payments"

    def get(self, request, pk, student_id):
        program = get_object_or_404(Program, pk=pk)
        student = get_object_or_404(Student, pk=student_id)

        # Object level check for Parents
        from .permission_views import get_user_role

        if get_user_role(request.user) == "Parent":
            try:
                adult = request.user.adult_profile
                if student not in adult.students.all():
                    messages.error(
                        request,
                        "You do not have permission to view this balance sheet.",
                    )
                    return redirect("home")
            except Exception:
                messages.error(
                    request, "You do not have permission to view this balance sheet."
                )
                return redirect("home")
        # Ensure enrollment
        if not Enrollment.objects.filter(student=student, program=program).exists():
            messages.error(request, f"{student} is not enrolled in {program}.")
            return redirect("program_detail", pk=program.pk)

        # Gather entries similar to balance sheet
        entries = []
        fees = Fee.objects.filter(program=program)
        for fee in fees:
            if (
                fee.assignments.exists()
                and not fee.assignments.filter(student=student).exists()
            ):
                continue
            fee_date = fee.date or (fee.created_at.date() if fee.created_at else None)
            entries.append(
                {
                    "date": fee_date,
                    "type": "Fee",
                    "name": fee.name,
                    "amount": fee.amount,
                }
            )
        # Sliding scale: include if exists and user has permission
        from .permission_views import can_user_read

        can_view_sliding = can_user_read(request.user, "sliding_scale")
        sliding = SlidingScale.objects.filter(student=student, program=program).first()
        from decimal import Decimal

        total_fees_for_discount = sum(
            [fee.amount for fee in Fee.objects.filter(program=program)],
            start=Decimal("0"),
        )
        if sliding and sliding.percent is not None and can_view_sliding:
            discount = compute_sliding_discount_rounded(
                total_fees_for_discount, sliding.percent
            )
            entries.append(
                {
                    "date": sliding.date,
                    "type": "Sliding Scale",
                    "name": f"Sliding scale ({sliding.percent}%)",
                    "amount": -discount,
                }
            )
        payments = Payment.objects.filter(student=student, program=program)
        for p in payments:
            via = dict(Payment.PAID_VIA_CHOICES).get(p.paid_via, p.paid_via)
            details = (
                f" (check #{p.check_number})"
                if (p.paid_via == "check" and p.check_number)
                else ""
            )
            if p.paid_via == "other" and p.notes:
                details += f" — {p.notes}"
            entries.append(
                {
                    "date": p.paid_on,
                    "type": "Payment",
                    "name": f"Payment via {via}{details}",
                    "amount": -p.amount,
                    "payment_id": p.id,
                }
            )

        entries.sort(key=lambda e: (e["date"] is None, e["date"], e["type"]))

        total_fees = sum([e["amount"] for e in entries if e["type"] == "Fee"])
        total_sliding = -sum(
            [e["amount"] for e in entries if e["type"] == "Sliding Scale"]
        )
        total_payments = -sum([e["amount"] for e in entries if e["type"] == "Payment"])
        balance = total_fees - total_sliding - total_payments

        from django.shortcuts import render

        return render(
            request,
            "programs/balance_sheet_print.html",
            {
                "program": program,
                "student": student,
                "entries": entries,
                "total_fees": total_fees,
                "total_sliding": total_sliding,
                "total_payments": total_payments,
                "balance": balance,
            },
        )


class ProgramFeeSelectView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "programs.change_fee"
    template_name = "programs/fee_select.html"

    def dispatch(self, request, *args, **kwargs):
        self.program = get_object_or_404(Program, pk=kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, pk):
        from django.shortcuts import render

        fees = Fee.objects.filter(program=self.program).order_by("name")
        return render(
            request, self.template_name, {"program": self.program, "fees": fees}
        )


class ProgramFeeAssignmentEditView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "programs.change_fee"
    template_name = "programs/fee_assignment_form.html"

    def dispatch(self, request, *args, **kwargs):
        self.program = get_object_or_404(Program, pk=kwargs["pk"])
        self.fee = get_object_or_404(Fee, pk=kwargs["fee_id"], program=self.program)
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, pk, fee_id):
        form = FeeAssignmentEditForm(program=self.program, fee=self.fee)
        from django.shortcuts import render

        return render(
            request,
            self.template_name,
            {"program": self.program, "fee": self.fee, "form": form},
        )

    def post(self, request, pk, fee_id):
        form = FeeAssignmentEditForm(request.POST, program=self.program, fee=self.fee)
        if form.is_valid():
            form.save()
            messages.success(request, "Fee applicability saved.")
            return redirect(
                "program_fee_assignments", pk=self.program.pk, fee_id=self.fee.pk
            )
        from django.shortcuts import render

        return render(
            request,
            self.template_name,
            {"program": self.program, "fee": self.fee, "form": form},
        )


class ProgramFeeCreateView(
    LogFormSaveMixin,
    LoginRequiredMixin,
    PermissionRequiredMixin,
    DynamicWritePermissionMixin,
    CreateView,
):
    permission_required = "programs.add_fee"
    model = Fee
    form_class = FeeForm
    template_name = "programs/fee_form.html"
    section = "fees"

    def dispatch(self, request, *args, **kwargs):
        self.program = get_object_or_404(Program, pk=kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["program"] = self.program
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from .permission_views import get_user_role

        ctx["role"] = get_user_role(self.request.user)
        ctx["program"] = self.program
        ctx["is_create"] = True
        return ctx

    def form_valid(self, form):
        messages.success(self.request, "Fee created.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse(
            "program_fee_assignments",
            kwargs={"pk": self.program.pk, "fee_id": self.object.pk},
        )


class ProgramFeeUpdateView(
    LogFormSaveMixin,
    LoginRequiredMixin,
    PermissionRequiredMixin,
    DynamicWritePermissionMixin,
    UpdateView,
):
    permission_required = "programs.change_fee"
    model = Fee
    form_class = FeeForm
    template_name = "programs/fee_form.html"
    pk_url_kwarg = "fee_id"
    section = "fees"

    def dispatch(self, request, *args, **kwargs):
        self.program = get_object_or_404(Program, pk=kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_object(self, queryset=None):
        return get_object_or_404(Fee, pk=self.kwargs["fee_id"], program=self.program)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["program"] = self.program
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["program"] = self.program
        ctx["is_create"] = False
        return ctx

    def form_valid(self, form):
        messages.success(self.request, "Fee updated.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse(
            "program_fee_assignments",
            kwargs={"pk": self.program.pk, "fee_id": self.object.pk},
        )


class ApplyProgramSelectView(View):
    template_name = "apply/select_program.html"

    def get(self, request):
        from django.shortcuts import render
        from django.utils import timezone

        # Show active programs grouped by timing (future/current/past)
        today = timezone.localdate()
        programs = list(Program.objects.filter(active=True))

        def status(prog):
            sd = prog.start_date
            ed = prog.end_date
            if sd and sd > today:
                return "future"
            if ed and ed < today:
                return "past"
            # If only start or only end or none: treat as current if not clearly future/past
            return "current"

        def sort_key(prog):
            sd = prog.start_date
            return (sd is None, sd or today, prog.name or "")

        future_programs = sorted(
            [p for p in programs if status(p) == "future"], key=sort_key
        )
        current_programs = sorted(
            [p for p in programs if status(p) == "current"], key=sort_key
        )
        past_programs = sorted(
            [p for p in programs if status(p) == "past"], key=sort_key
        )
        # Keep form in context for possible fallback
        form = ProgramApplySelectForm()
        return render(
            request,
            self.template_name,
            {
                "form": form,
                "future_programs": future_programs,
                "current_programs": current_programs,
                "past_programs": past_programs,
            },
        )

    def post(self, request):
        from django.shortcuts import redirect, render

        form = ProgramApplySelectForm(request.POST)
        if form.is_valid():
            program = form.cleaned_data["program"]
            return redirect("apply_program", program_id=program.pk)
        return render(request, self.template_name, {"form": form})


class ApplyStudentView(View):
    template_name = "apply/form.html"

    def _program_status(self, program):
        from django.utils import timezone

        today = timezone.localdate()
        sd = program.start_date
        ed = program.end_date
        if sd and sd > today:
            return "future"
        if ed and ed < today:
            return "past"
        return "current"

    def get(self, request, program_id):
        from django.shortcuts import get_object_or_404, redirect, render

        program = get_object_or_404(Program, pk=program_id)
        status = self._program_status(program)
        if status != "future":
            if status == "current":
                messages.info(
                    request,
                    "Applications for this program are closed. For current programs, please contact us at info@girlsofsteelrobotics.org.",
                )
            else:
                messages.error(request, "Applications are closed for this program.")
            return redirect("apply_start")
        form = StudentApplicationForm(initial={"program": program})
        return render(request, self.template_name, {"form": form, "program": program})

    def post(self, request, program_id):
        from django.shortcuts import get_object_or_404, redirect, render

        program = get_object_or_404(Program, pk=program_id)
        status = self._program_status(program)
        if status != "future":
            if status == "current":
                messages.info(
                    request,
                    "Applications for this program are closed. For current programs, please contact us at info@girlsofsteelrobotics.org.",
                )
            else:
                messages.error(request, "Applications are closed for this program.")
            return redirect("apply_start")
        form = StudentApplicationForm(request.POST)
        if form.is_valid():
            app = form.save()
            messages.success(
                request, "Application submitted! We will be in touch soon."
            )
            return redirect("apply_thanks")
        return render(request, self.template_name, {"form": form, "program": program})


class ApplyThanksView(View):
    template_name = "apply/thanks.html"

    def get(self, request):
        from django.shortcuts import render

        return render(request, self.template_name)


class ProgramEmailBalancesView(LoginRequiredMixin, DynamicReadPermissionMixin, View):
    section = "programs"
    permission_required = "programs.view_program"
    template_name = "programs/email_balances_form.html"

    def get(self, request, pk):
        program = get_object_or_404(Program, pk=pk)
        form = ProgramEmailBalancesForm(program=program)
        return self._render(request, form, program)

    def post(self, request, pk):
        program = get_object_or_404(Program, pk=pk)
        form = ProgramEmailBalancesForm(request.POST, program=program)
        if not form.is_valid():
            return self._render(request, form, program)

        subject = form.cleaned_data["subject"]
        default_message = form.cleaned_data.get("default_message") or ""
        include_zero = form.cleaned_data.get("include_zero_balances") or False
        test_email = form.cleaned_data.get("test_email")

        # Build sender connection (reuse logic from ProgramEmailView)
        selected = form.cleaned_data.get("from_account")
        accounts = getattr(settings, "EMAIL_SENDER_ACCOUNTS", []) or []
        acc = None
        if accounts and selected and selected != "DEFAULT":
            for a in accounts:
                key = a.get("key") or a.get("email")
                if key == selected:
                    acc = a
                    break
        conn_kwargs = {
            "backend": getattr(
                settings, "EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend"
            ),
            "host": getattr(settings, "EMAIL_HOST", ""),
            "port": getattr(settings, "EMAIL_PORT", 465),
            "use_tls": getattr(settings, "EMAIL_USE_TLS", False),
            "use_ssl": getattr(settings, "EMAIL_USE_SSL", True),
            "timeout": getattr(settings, "EMAIL_TIMEOUT", 10),
        }
        if acc:
            conn_kwargs.update(
                {
                    "username": acc.get("username") or "",
                    "password": acc.get("password") or "",
                }
            )
            from_email = acc.get("email") or getattr(
                settings, "DEFAULT_FROM_EMAIL", "no-reply@example.com"
            )
            # Include display_name name if provided
            display_name = acc.get("display_name")
            if display_name:
                from_email = f'"{display_name}" <{from_email}>'
        else:
            conn_kwargs.update(
                {
                    "username": getattr(settings, "EMAIL_HOST_USER", ""),
                    "password": getattr(settings, "EMAIL_HOST_PASSWORD", ""),
                }
            )
            from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@example.com")
        connection = get_connection(**conn_kwargs)

        # Collect students enrolled in program
        students = (
            Student.objects.filter(enrollment__program=program)
            .select_related("school")
            .order_by(
                Lower(Coalesce("first_name", "legal_first_name")), Lower("last_name")
            )
        )

        # Helper to compute balance and entries like ProgramStudentBalanceView
        from decimal import Decimal

        from .permission_views import can_user_read

        can_view_sliding = can_user_read(self.request.user, "sliding_scale")

        def compute_entries_and_balance(student):
            entries = []
            fees = Fee.objects.filter(program=program)
            for fee in fees:
                if (
                    fee.assignments.exists()
                    and not fee.assignments.filter(student=student).exists()
                ):
                    continue
                fee_date = fee.date or (
                    fee.created_at.date() if fee.created_at else None
                )
                entries.append(
                    {
                        "date": fee_date,
                        "type": "Fee",
                        "name": fee.name,
                        "amount": fee.amount,
                    }
                )
            sliding = SlidingScale.objects.filter(
                student=student, program=program
            ).first()
            total_fees_for_discount = sum(
                [fee.amount for fee in Fee.objects.filter(program=program)],
                start=Decimal("0"),
            )
            if sliding and sliding.percent is not None and can_view_sliding:
                discount = compute_sliding_discount_rounded(
                    total_fees_for_discount, sliding.percent
                )
                entries.append(
                    {
                        "date": sliding.created_at.date(),
                        "type": "Sliding Scale",
                        "name": f"Sliding scale ({sliding.percent}%)",
                        "amount": -discount,
                    }
                )
            payments = Payment.objects.filter(student=student, program=program)
            for p in payments:
                via = dict(Payment.PAID_VIA_CHOICES).get(p.paid_via, p.paid_via)
                details = (
                    f" (check #{p.check_number})"
                    if (p.paid_via == "check" and p.check_number)
                    else ""
                )
                if p.paid_via == "other" and p.notes:
                    details += f" — {p.notes}"
                entries.append(
                    {
                        "date": p.paid_on,
                        "type": "Payment",
                        "name": f"Payment via {via}{details}",
                        "amount": -p.amount,
                        "payment_id": p.id,
                    }
                )
            entries.sort(key=lambda e: (e["date"] is None, e["date"], e["type"]))
            total_fees = sum([e["amount"] for e in entries if e["type"] == "Fee"])
            total_sliding = -sum(
                [e["amount"] for e in entries if e["type"] == "Sliding Scale"]
            )
            total_payments = -sum(
                [e["amount"] for e in entries if e["type"] == "Payment"]
            )
            balance = total_fees - total_sliding - total_payments
            return entries, total_fees, total_sliding, total_payments, balance

        # Build list of targets with non-empty recipient emails
        targets = []
        for s in students:
            entries, total_fees, total_sliding, total_payments, balance = (
                compute_entries_and_balance(s)
            )
            if not include_zero and balance == 0:
                continue
            # Gather recipient emails: only parents/guardians who opted in for updates
            emails = []
            for adult in s.all_parents:
                # Only include parents/guardians who have opted into email updates and are active
                if getattr(adult, "email_updates", False) and getattr(
                    adult, "active", True
                ):
                    email = adult.personal_email or adult.email or adult.andrew_email
                    if email:
                        emails.append(email)
            # Deduplicate while preserving order
            seen = set()
            deduped = []
            for e in emails:
                if e and e not in seen:
                    deduped.append(e)
                    seen.add(e)
            if not deduped:
                continue
            targets.append(
                {
                    "student": s,
                    "emails": deduped,
                    "entries": entries,
                    "total_fees": total_fees,
                    "total_sliding": total_sliding,
                    "total_payments": total_payments,
                    "balance": balance,
                }
            )

        if not targets and not test_email:
            messages.error(request, "No recipients found to email.")
            return self._render(request, form, program)

        # Prepare sending: if test, pick first student's content or a generic minimal body
        to_send = []
        if test_email:
            sample = targets[0] if targets else None
            if sample is None:
                messages.error(
                    request, "No sample data available to send a test email."
                )
                return self._render(request, form, program)
            to_send.append((test_email, sample))
        else:
            for t in targets:
                # send one email to combined recipients per student
                to_send.append((t["emails"], t))

        sent_total = 0
        for dest, data in to_send:
            # Render balance sheet HTML
            ctx = {
                "program": program,
                "student": data["student"],
                "entries": data["entries"],
                "total_fees": data["total_fees"],
                "total_sliding": data["total_sliding"],
                "total_payments": data["total_payments"],
                "balance": data["balance"],
            }
            # Include optional rich-text message inside the template so styles apply correctly
            ctx["message_html"] = default_message or ""
            balance_html = render_to_string(
                "programs/balance_sheet_email.html", ctx, request=None
            )
            full_html = balance_html
            try:
                inlined_html = transform(full_html)
            except Exception:
                inlined_html = full_html
            text_body = strip_tags(inlined_html)

            # Ensure dest is a list of flat email strings
            if isinstance(dest, str):
                to_list = [dest]
            else:
                to_list = list(dest)
            # Normalize: strip and drop empties/None
            to_list = [str(e).strip() for e in to_list if e and str(e).strip()]
            if not to_list:
                logger.warning(
                    "ProgramEmailBalances: no valid recipient emails for %s; skipping",
                    data["student"],
                )
                continue

            # Place all adult emails in To; only archive address in BCC
            to_addr = to_list
            bcc = ["swithee@andrew.cmu.edu"]

            email = EmailMultiAlternatives(
                subject=subject,
                body=text_body,
                from_email=from_email,
                to=to_addr,
                bcc=bcc,
                connection=connection,
            )
            email.attach_alternative(inlined_html, "text/html")
            try:
                sent = email.send(fail_silently=False)
                sent_total += sent
            except Exception as e:
                logger.error(
                    "ProgramEmailBalances: send failed for %s | error=%s",
                    data["student"],
                    e,
                    exc_info=True,
                )

        if test_email:
            messages.success(request, f"Test email sent to {test_email}.")
        else:
            messages.success(
                request, f"Balance emails queued/sent for {len(to_send)} student(s)."
            )
        return redirect("program_dues_owed", pk=program.pk)

    def _render(self, request, form, program):
        from django.shortcuts import render

        return render(
            request,
            self.template_name,
            {
                "program": program,
                "form": form,
            },
        )


class ProgramDuesOwedView(LoginRequiredMixin, DynamicReadPermissionMixin, View):
    """
    Lists all students enrolled in a specific program and the total amount each currently owes
    for that program, using the same balance computation as the per-program balance sheet.
    """

    template_name = "programs/dues_owed.html"
    section = "programs"

    def _program_balance_for_student(self, student, program):
        # Reproduce ProgramStudentBalanceView totals for a given student+program
        from decimal import Decimal

        # Fees applicable to the student (respect fee assignments)
        applicable_fees = []
        for fee in Fee.objects.filter(program=program):
            if (
                fee.assignments.exists()
                and not fee.assignments.filter(student=student).exists()
            ):
                continue
            applicable_fees.append(fee.amount)
        total_fees = sum(applicable_fees, start=Decimal("0"))

        # Sliding scale percent discount based on total program fees (per balance sheet logic)
        from .permission_views import can_user_read

        can_view_sliding = can_user_read(self.request.user, "sliding_scale")
        sliding = SlidingScale.objects.filter(student=student, program=program).first()
        total_fees_for_discount = sum(
            [f.amount for f in Fee.objects.filter(program=program)], start=Decimal("0")
        )
        total_sliding = Decimal("0")
        if sliding and sliding.percent is not None and can_view_sliding:
            total_sliding = compute_sliding_discount_rounded(
                total_fees_for_discount, sliding.percent
            )

        # Payments made by student for this program
        total_payments = sum(
            [
                p.amount
                for p in Payment.objects.filter(student=student, program=program)
            ],
            start=Decimal("0"),
        )

        balance = total_fees - total_sliding - total_payments
        return balance

    def get(self, request, pk):
        from django.shortcuts import render

        program = get_object_or_404(Program, pk=pk)
        # Only active students enrolled in this program
        students = (
            Student.objects.filter(enrollment__program=program, active=True)
            .select_related("school")
            .order_by(
                Lower(Coalesce("first_name", "legal_first_name")), Lower("last_name")
            )
        )

        rows = []
        grand_total = 0
        for s in students:
            balance_sum = self._program_balance_for_student(s, program)
            rows.append(
                {
                    "student": s,
                    "amount_owed": balance_sum,
                }
            )
            grand_total += balance_sum

        return render(
            request,
            self.template_name,
            {
                "program": program,
                "rows": rows,
                "grand_total": grand_total,
            },
        )


class ProgramSignoutSheetView(LoginRequiredMixin, DynamicReadPermissionMixin, View):
    template_name = "programs/signout_sheet.html"
    section = "programs"

    def get(self, request, pk):
        from django.shortcuts import render

        program = get_object_or_404(Program, pk=pk)
        # Fetch students enrolled in the program, active first, then inactive
        base_qs = (
            program.students.select_related("user")
            .all()
            .annotate(
                sort_first=Lower(Coalesce("first_name", "legal_first_name")),
                sort_last=Lower("last_name"),
            )
        )
        active_students = list(
            base_qs.filter(active=True).order_by("sort_first", "sort_last")
        )
        inactive_students = list(
            base_qs.filter(active=False).order_by("sort_first", "sort_last")
        )
        students = active_students
        ctx = {
            "program": program,
            "students": students,
        }
        return render(request, self.template_name, ctx)


class ProgramSchoolsView(LoginRequiredMixin, DynamicReadPermissionMixin, View):
    template_name = "programs/schools.html"
    section = "programs"

    def get(self, request, pk):
        from django.shortcuts import render

        program = get_object_or_404(Program, pk=pk)
        # Active students enrolled in this program, grouped by school
        students = (
            Student.objects.filter(enrollment__program=program, active=True)
            .select_related("school")
            .annotate(
                sort_first=Coalesce("first_name", "legal_first_name"),
            )
            .order_by("school__name", Lower("sort_first"), Lower("last_name"))
        )
        grouped = {}
        for s in students:
            label = s.school.name if s.school_id else "No School"
            grouped.setdefault(label, []).append(s)
        grouped_items = sorted(
            grouped.items(), key=lambda kv: (kv[0] == "No School", kv[0] or "")
        )
        return render(
            request,
            self.template_name,
            {
                "program": program,
                "grouped": grouped_items,
            },
        )


class ProgramStudentMapView(LoginRequiredMixin, DynamicReadPermissionMixin, View):
    template_name = "programs/map.html"
    section = "programs"

    def get(self, request, pk):
        from django.shortcuts import render

        program = get_object_or_404(Program, pk=pk)
        # Active students enrolled in this program with some address info
        students = (
            Student.objects.filter(programs=program, active=True)
            .only(
                "first_name",
                "legal_first_name",
                "last_name",
                "address",
                "city",
                "state",
                "zip_code",
            )
            .annotate(sort_first=Coalesce("first_name", "legal_first_name"))
            .order_by(Lower("sort_first"), Lower("last_name"))
        )
        items = []
        for s in students:
            parts = [s.address or "", s.city or "", s.state or "", s.zip_code or ""]
            addr = ", ".join([p for p in parts if p]).strip(", ")
            if not addr:
                continue
            name = f"{(s.first_name or s.legal_first_name or '').strip()} {s.last_name}".strip()
            items.append(
                {
                    "name": name or f"Student #{s.pk}",
                    "address": addr,
                }
            )
        return render(
            request,
            self.template_name,
            {
                "program": program,
                "items": items,
            },
        )


class AdultsListView(LoginRequiredMixin, DynamicReadPermissionMixin, ListView):
    model = Adult
    template_name = "adults/list.html"
    context_object_name = "adults"
    section = "adult_info"

    def get_queryset(self):
        from .permission_views import get_user_role

        role = get_user_role(self.request.user)
        if role == "Parent":
            try:
                adult = self.request.user.adult_profile
                return Adult.objects.filter(pk=adult.pk).prefetch_related("students")
            except (Adult.DoesNotExist, AttributeError):
                return Adult.objects.none()
        return Adult.objects.all().prefetch_related("students")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from .permission_views import get_user_role

        ctx["role"] = get_user_role(self.request.user)
        return ctx


class AdultUpdateView(
    LogFormSaveMixin,
    LoginRequiredMixin,
    PermissionRequiredMixin,
    DynamicWritePermissionMixin,
    UpdateView,
):
    model = Adult
    form_class = AdultForm
    template_name = "adults/form.html"
    permission_required = "programs.change_adult"
    section = "adult_info"

    def get_success_url(self):
        nxt = self.request.GET.get("next")
        if nxt:
            return nxt
        return reverse("adult_list")
