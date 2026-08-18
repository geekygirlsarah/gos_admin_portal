from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.mail import EmailMultiAlternatives, get_connection
from django.db.models import Q, Value
from django.db.models.functions import Coalesce, Lower, NullIf
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.html import strip_tags
from django.views.generic import CreateView, DetailView, ListView, UpdateView, View
from premailer import transform

from ..forms import (
    AddExistingStudentToProgramForm,
    ProgramDocumentForm,
    ProgramEmailForm,
    ProgramForm,
    QuickCreateStudentForm,
)
from ..models import (
    Adult,
    Crew,
    Enrollment,
    Program,
    ProgramDocument,
    Student,
    SubTeam,
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
    active_students_in_program,
    get_safe_url,
    redirect_back,
    resolve_address_points,
)
from .mixins import (
    DynamicReadPermissionMixin,
    DynamicWritePermissionMixin,
    LogFormSaveMixin,
    StudentQuerysetRoleMixin,
    logger,
)


class ProgramListView(LoginRequiredMixin, DynamicReadPermissionMixin, ListView):
    model = Program
    template_name = "home.html"  # landing page
    context_object_name = "programs"
    section = "programs"

    def get_queryset(self):
        # Keep a base queryset; ordering will be handled in context via grouping
        qs = Program.objects.all()

        role = get_user_role(self.request.user)
        if role == "Mentor":
            # Only show active programs to Mentors
            today = timezone.localdate()
            qs = (
                qs.filter(active=True)
                .filter(Q(start_date__isnull=True) | Q(start_date__lte=today))
                .filter(Q(end_date__isnull=True) | Q(end_date__gte=today))
            )
        elif role in ("Student", "Parent", "Alumni"):
            # Students and Parents should not see the program list
            return Program.objects.none()
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

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

        future = sorted(
            [p for p in programs if status(p) == "future"],
            key=lambda p: p.name or "",
        )
        future.sort(
            key=lambda p: (p.start_date is not None, p.start_date), reverse=True
        )

        current = sorted(
            [p for p in programs if status(p) == "current"],
            key=lambda p: p.name or "",
        )
        current.sort(key=lambda p: (p.end_date is not None, p.end_date), reverse=True)

        past = sorted(
            [p for p in programs if status(p) == "past"],
            key=lambda p: p.name or "",
        )
        past.sort(key=lambda p: (p.end_date is not None, p.end_date), reverse=True)

        # Group past programs by school year (July–June) based on end date,
        # newest school year first.
        def school_year_label(prog):
            ed = prog.end_date
            if ed:
                start = ed.year if ed.month >= 7 else ed.year - 1
                return f"{start}-{start + 1}"
            sd = prog.start_date
            if sd:
                return str(sd.year)
            return "Unknown"

        past_grouped = {}
        for p in past:
            past_grouped.setdefault(school_year_label(p), []).append(p)
        for label in past_grouped:
            past_grouped[label].sort(
                key=lambda p: (p.end_date is not None, p.end_date), reverse=True
            )
        past_programs_by_year = sorted(
            past_grouped.items(), key=lambda kv: kv[0], reverse=True
        )

        ctx.update(
            {
                "future_programs": future,
                "current_programs": current,
                "past_programs": past,
                "past_programs_by_year": past_programs_by_year,
            }
        )
        return ctx


class ProgramStudentPhotoListView(
    LoginRequiredMixin, StudentQuerysetRoleMixin, ListView
):
    model = Enrollment
    template_name = "students/photo_grid.html"
    context_object_name = "enrollments"
    paginate_by = 48

    def dispatch(self, request, *args, **kwargs):
        self.program = get_object_or_404(Program, pk=kwargs.get("pk"))
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        qs = Enrollment.objects.filter(program=self.program).select_related(
            "student", "team", "crew"
        )

        qs = self.filter_students_by_role(
            qs,
            adults_field="student__adults",
            student_field="student",
            empty_queryset=Enrollment.objects.none(),
        )

        return qs.annotate(
            sort_first=Lower(
                Coalesce(
                    NullIf("student__first_name", Value("")),
                    "student__legal_first_name",
                )
            ),
            sort_last=Lower("student__last_name"),
        ).order_by("sort_first", "sort_last")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["program"] = self.program
        # Compatibility for the template which expects 'students'
        ctx["students"] = ctx["enrollments"]
        # Split the page's enrollments into active and inactive sections so
        # inactive (dropped/graduated) students aren't mixed in with active ones.
        page_enrollments = list(ctx["enrollments"])
        ctx["active_enrollments"] = [
            e for e in page_enrollments if e.active and not e.student.graduated
        ]
        ctx["inactive_enrollments"] = [
            e for e in page_enrollments if not (e.active and not e.student.graduated)
        ]
        return ctx


class ProgramEmergencyContactsView(
    LoginRequiredMixin, MentorOrLeadMentorRequiredMixin, View
):
    """Lists active students in a program with their contact info, plus the
    email and phone number of every parent/guardian on file for each student.

    Each student is shown with their Primary Guardian, Secondary Guardian,
    and any other parents/guardians on file."""

    template_name = "programs/emergency_contacts.html"

    def _split_guardians(self, student):
        """Return (primary, secondary, others) guardians for a student.

        Falls back to the first/second parents on file when the
        primary_contact/secondary_contact fields aren't set.
        """
        parents = student.all_parents
        primary = student.primary_contact or (parents[0] if parents else None)
        remaining = [p for p in parents if p.pk != getattr(primary, "pk", None)]
        secondary = student.secondary_contact or (remaining[0] if remaining else None)
        others = [p for p in remaining if p.pk != getattr(secondary, "pk", None)]
        return primary, secondary, others

    def get(self, request, program_id):
        program = get_object_or_404(Program, pk=program_id)
        students = (
            active_students_in_program(program)
            .select_related(
                "primary_contact_relationship__adult",
                "secondary_contact_relationship__adult",
                "school",
            )
            .prefetch_related("adults", "adultstudentrelationship_set")
            .annotate(
                sort_first=Coalesce(
                    NullIf("first_name", Value("")), "legal_first_name"
                ),
            )
            .order_by(Lower("sort_first"), Lower("last_name"))
        )
        rows = []
        for student in students:
            primary, secondary, others = self._split_guardians(student)
            rows.append(
                {
                    "student": student,
                    "primary": primary,
                    "secondary": secondary,
                    "others": others,
                }
            )
        return render(
            request,
            self.template_name,
            {"program": program, "rows": rows},
        )


class ProgramDetailView(LoginRequiredMixin, DynamicReadPermissionMixin, DetailView):
    model = Program
    template_name = "programs/detail.html"
    context_object_name = "program"
    section = "programs"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        ctx["role"] = get_user_role(self.request.user)
        program = self.object

        role = ctx["role"]

        # Prepare annotated queryset for consistent sorting
        base_qs = (
            Enrollment.objects.filter(program=program)
            .select_related("student", "student__user", "team", "crew")
            .annotate(
                sort_first=Lower(
                    Coalesce(
                        NullIf("student__first_name", Value("")),
                        "student__legal_first_name",
                    )
                ),
                sort_last=Lower("student__last_name"),
            )
        )

        # Parent restriction
        if role == "Parent":
            try:
                adult = self.request.user.adult_profile
                base_qs = base_qs.filter(student__adults=adult)
            except (Adult.DoesNotExist, AttributeError):
                base_qs = Enrollment.objects.none()

        # Split into active and inactive sections
        ctx["active_enrollments"] = base_qs.filter(
            active=True, student__graduated=False
        ).order_by("sort_first", "sort_last")
        ctx["inactive_enrollments"] = base_qs.exclude(
            active=True, student__graduated=False
        ).order_by("sort_first", "sort_last")

        # Backwards compatibility (old templates may rely on a single list)
        ctx["active_students"] = [e.student for e in ctx["active_enrollments"]]
        ctx["inactive_students"] = [e.student for e in ctx["inactive_enrollments"]]
        ctx["enrolled_students"] = ctx["active_students"] + ctx["inactive_students"]

        ctx["teams"] = Team.objects.all()
        ctx["crews"] = program.crews.all()

        if role == "Mentor":
            ctx["can_manage_students"] = False
            ctx["can_add_payment"] = False
            ctx["can_manage_fees"] = False
            ctx["can_view_payments"] = False
            ctx["can_view_attendance"] = False
            ctx["can_view_documents"] = can_user_read(
                self.request.user, "student_documents"
            )
        else:
            ctx["can_manage_students"] = can_user_write(
                self.request.user, "student_info"
            )
            ctx["can_add_payment"] = can_user_write(self.request.user, "payments")
            ctx["can_manage_fees"] = can_user_write(self.request.user, "fees")
            ctx["can_view_payments"] = can_user_read(self.request.user, "payments")
            ctx["can_view_attendance"] = can_user_read(self.request.user, "attendance")
            ctx["can_view_documents"] = can_user_read(
                self.request.user, "student_documents"
            )

        # Document management: any user who can edit the program can manage
        # the blank documents attached to it (used by the application wizard
        # Step 9 signed-document upload flow).
        ctx["can_manage_documents"] = self.request.user.has_perm(
            "programs.change_program"
        )
        ctx["program_documents"] = program.documents.all().order_by(
            "display_order", "name"
        )

        if ctx["can_manage_students"]:
            ctx["add_existing_form"] = AddExistingStudentToProgramForm(program=program)
            ctx["quick_create_form"] = QuickCreateStudentForm()
        return ctx


class ProgramStudentDocumentsView(
    LoginRequiredMixin, DynamicReadPermissionMixin, DetailView
):
    model = Program
    template_name = "programs/student_documents.html"
    context_object_name = "program"
    section = "student_documents"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        program = self.object

        # Get all required documents for this program
        docs = program.documents.all().order_by("display_order", "name")
        ctx["program_documents"] = docs

        # Get all students enrolled in this program
        enrollments = (
            Enrollment.objects.filter(program=program)
            .select_related("student")
            .prefetch_related("student__signed_documents")
            .annotate(
                sort_first=Lower(
                    Coalesce(
                        NullIf("student__first_name", Value("")),
                        "student__legal_first_name",
                    )
                ),
                sort_last=Lower("student__last_name"),
            )
            .order_by("sort_last", "sort_first")
        )

        # Build a matrix of student -> {doc_id: signed_doc}
        student_docs = []
        for e in enrollments:
            student = e.student
            submissions = {
                sd.program_document_id: sd for sd in student.signed_documents.all()
            }
            student_docs.append(
                {
                    "student": student,
                    "submissions": submissions,
                    "active": e.active and not student.graduated,
                }
            )

        ctx["student_docs"] = student_docs
        ctx["role"] = get_user_role(self.request.user)
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

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        program = self.object
        ctx["can_manage_documents"] = self.request.user.has_perm(
            "programs.change_program"
        )
        ctx["program_documents"] = program.documents.all().order_by(
            "display_order", "name"
        )
        return ctx

    def get_success_url(self):
        return reverse("program_detail", args=[self.object.pk])


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


class ProgramEnrollmentUpdateView(LoginRequiredMixin, LeadMentorRequiredMixin, View):
    def post(self, request, pk):
        enrollment_id = request.POST.get("enrollment_id")
        team_id = request.POST.get("team_id")
        crew_id = request.POST.get("crew_id")
        subteam_id = request.POST.get("subteam_id")
        active = request.POST.get("active")
        enrollment = get_object_or_404(Enrollment, id=enrollment_id, program_id=pk)

        updated_fields = []
        if active is not None:
            new_active = active.lower() == "true"
            if enrollment.active != new_active:
                enrollment.active = new_active
                updated_fields.append("Active status")

        if team_id is not None:
            if team_id:
                team = get_object_or_404(Team, id=team_id)
                enrollment.team = team
            else:
                enrollment.team = None
            updated_fields.append("Team")

        if crew_id is not None:
            if crew_id:
                crew = get_object_or_404(Crew, id=crew_id, program_id=pk)
                enrollment.crew = crew
            else:
                enrollment.crew = None
            updated_fields.append("Crew")

        if subteam_id is not None:
            if subteam_id:
                subteam = get_object_or_404(SubTeam, id=subteam_id, program_id=pk)
                enrollment.subteam = subteam
            else:
                enrollment.subteam = None
            updated_fields.append("SubTeam")

        enrollment.save()
        if updated_fields:
            messages.success(
                request,
                f"{' and '.join(updated_fields)} updated for {enrollment.student}.",
            )
        next_url = request.POST.get("next")
        safe_url = get_safe_url(request, next_url)
        if safe_url:
            return redirect(safe_url)
        return redirect("program_detail", pk=pk)


class ProgramStudentRemoveView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "programs.change_student"

    def post(self, request, pk, student_id):
        program = get_object_or_404(Program, pk=pk)
        student = get_object_or_404(Student, pk=student_id)
        Enrollment.objects.filter(student=student, program=program).delete()
        messages.success(request, f"Removed {student} from {program}.")
        return redirect("program_detail", pk=program.pk)


class ProgramAssignmentView(LoginRequiredMixin, LeadMentorRequiredMixin, View):
    template_name = "programs/assignment.html"

    def get(self, request, pk):
        program = get_object_or_404(Program, pk=pk)
        enrollments = Enrollment.objects.filter(program=program).select_related(
            "student", "team", "crew", "subteam"
        )
        teams = Team.objects.all().order_by("number")
        crews = Crew.objects.filter(program=program).order_by("name")
        subteams = SubTeam.objects.filter(program=program).order_by("name")

        # Separate inactive students (inactive enrollment or graduated) so they
        # don't get mixed in with the active ones being assigned.
        active_enrollments = enrollments.filter(active=True, student__graduated=False)
        inactive_enrollments = enrollments.exclude(
            active=True, student__graduated=False
        )

        return render(
            request,
            self.template_name,
            {
                "program": program,
                "enrollments": enrollments,
                "active_enrollments": active_enrollments,
                "inactive_enrollments": inactive_enrollments,
                "teams": teams,
                "crews": crews,
                "subteams": subteams,
            },
        )

    def post(self, request, pk):
        program = get_object_or_404(Program, pk=pk)
        assignment_type = request.POST.get("assignment_type")
        target_id = request.POST.get("target_id")
        student_ids = request.POST.getlist("student_ids")

        if not student_ids:
            messages.warning(request, "No students selected.")
            return redirect("program_assignment", pk=pk)

        enrollments = Enrollment.objects.filter(
            program=program, student_id__in=student_ids
        )

        if not target_id:
            if assignment_type == "team":
                enrollments.update(team=None)
                messages.success(
                    request,
                    f"Unassigned team from {len(student_ids)} student(s).",
                )
            elif assignment_type == "crew":
                enrollments.update(crew=None)
                messages.success(
                    request,
                    f"Unassigned crew from {len(student_ids)} student(s).",
                )
            elif assignment_type == "subteam":
                enrollments.update(subteam=None)
                messages.success(
                    request,
                    f"Unassigned subteam from {len(student_ids)} student(s).",
                )
        elif assignment_type == "team":
            team = get_object_or_404(Team, id=target_id)
            enrollments.update(team=team)
            messages.success(
                request, f"Assigned {len(student_ids)} students to team {team}."
            )
        elif assignment_type == "crew":
            crew = get_object_or_404(Crew, id=target_id, program=program)
            enrollments.update(crew=crew)
            messages.success(
                request, f"Assigned {len(student_ids)} students to crew {crew}."
            )
        elif assignment_type == "subteam":
            subteam = get_object_or_404(SubTeam, id=target_id, program=program)
            enrollments.update(subteam=subteam)
            messages.success(
                request, f"Assigned {len(student_ids)} students to subteam {subteam}."
            )

        return redirect("program_assignment", pk=pk)


class ProgramEmailView(LoginRequiredMixin, LeadMentorRequiredMixin, View):
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
                for s in Student.objects.filter(
                    enrollment__program=prog, enrollment__active=True, graduated=False
                ).distinct():
                    if s.personal_email:
                        recipients.add(s.personal_email)
                    elif s.andrew_email:
                        recipients.add(s.andrew_email)
            if "parents" in groups:
                for parent in Adult.objects.filter(
                    students__enrollment__program=prog,
                    students__enrollment__active=True,
                    email_updates=True,
                    active=True,
                ).distinct():
                    e = parent.personal_email or parent.andrew_email
                    if e:
                        recipients.add(e)
            if "mentors" in groups:
                for m in Adult.objects.filter(is_mentor=True, active=True):
                    e = m.personal_email or m.andrew_email
                    if e:
                        recipients.add(e)

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
        ctx = {"form": form, "program": program}
        return render(self.request, self.template_name, ctx)


class ProgramStudentMapView(LoginRequiredMixin, DynamicReadPermissionMixin, View):
    template_name = "programs/map.html"
    section = "programs"

    def get(self, request, pk):
        program = get_object_or_404(Program, pk=pk)
        role = get_user_role(request.user)
        # Active (non-graduated) students enrolled in this program with some
        # address info. Parents and students get the "carpool map" view, so
        # only include students who have consented to directory sharing.
        qs = Student.objects.filter(
            enrollment__program=program, enrollment__active=True, graduated=False
        )
        if role in ("Parent", "Student"):
            qs = qs.filter(directory_consent=True)
        students = (
            qs.distinct()
            .only(
                "first_name",
                "legal_first_name",
                "last_name",
                "address",
                "city",
                "state",
                "zip_code",
                "phone_number",
                "personal_email",
            )
            .annotate(
                sort_first=Coalesce(NullIf("first_name", Value("")), "legal_first_name")
            )
            .order_by(Lower("sort_first"), Lower("last_name"))
        )
        rows = []
        addresses = []
        for s in students:
            parts = [s.address or "", s.city or "", s.state or "", s.zip_code or ""]
            addr = ", ".join([p for p in parts if p]).strip(", ")
            if not addr:
                continue
            name = f"{(s.first_name or s.legal_first_name or '').strip()} {s.last_name}".strip()
            rows.append(
                (
                    name or f"Student #{s.pk}",
                    addr,
                    s.phone_number or "",
                    s.personal_email or "",
                )
            )
            addresses.append(addr)
        points = resolve_address_points(addresses) if addresses else {}
        items = [
            {
                "name": name,
                "address": addr,
                "phone": phone,
                "email": email,
                "latitude": points[addr][0] if points.get(addr) else None,
                "longitude": points[addr][1] if points.get(addr) else None,
            }
            for name, addr, phone, email in rows
        ]
        # For parent/student views, show names of students who opted out of
        # directory sharing in an "Unlisted Students" section below the map.
        unlisted = []
        if role in ("Parent", "Student"):
            unlisted_qs = (
                Student.objects.filter(
                    enrollment__program=program,
                    enrollment__active=True,
                    graduated=False,
                    directory_consent=False,
                )
                .distinct()
                .only("first_name", "legal_first_name", "last_name")
                .annotate(
                    sort_first=Coalesce(
                        NullIf("first_name", Value("")), "legal_first_name"
                    )
                )
                .order_by(Lower("sort_first"), Lower("last_name"))
            )
            for s in unlisted_qs:
                name = f"{(s.first_name or s.legal_first_name or '').strip()} {s.last_name}".strip()
                if name:
                    unlisted.append(name)
        if role in ("Parent", "Student"):
            back_url = reverse("profile_dashboard")
            back_label = "← Back to Dashboard"
        else:
            back_url = reverse("program_detail", args=[program.pk])
            back_label = "← Back to Program"
        return render(
            request,
            self.template_name,
            {
                "program": program,
                "items": items,
                "unlisted": unlisted,
                "back_url": back_url,
                "back_label": back_label,
            },
        )


class ProgramSignoutSheetView(LoginRequiredMixin, DynamicReadPermissionMixin, View):
    template_name = "programs/signout_sheet.html"
    section = "programs"

    def get(self, request, pk):
        program = get_object_or_404(Program, pk=pk)
        # Fetch students enrolled in the program, active first, then inactive
        base_qs = (
            program.students.select_related("user")
            .all()
            .annotate(
                sort_first=Lower(
                    Coalesce(NullIf("first_name", Value("")), "legal_first_name")
                ),
                sort_last=Lower("last_name"),
            )
        )
        students = list(
            base_qs.filter(
                enrollment__program=program, enrollment__active=True, graduated=False
            )
            .distinct()
            .order_by("sort_first", "sort_last")
        )
        ctx = {
            "program": program,
            "students": students,
        }
        return render(request, self.template_name, ctx)


class ProgramSchoolsView(LoginRequiredMixin, DynamicReadPermissionMixin, View):
    template_name = "programs/schools.html"
    section = "programs"

    def get(self, request, pk):
        program = get_object_or_404(Program, pk=pk)
        # Active (non-graduated) students enrolled in this program, grouped by school
        students = (
            Student.objects.filter(
                enrollment__program=program, enrollment__active=True, graduated=False
            )
            .distinct()
            .select_related("school")
            .annotate(
                sort_first=Coalesce(
                    NullIf("first_name", Value("")), "legal_first_name"
                ),
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


class ProgramStudentExportView(LoginRequiredMixin, DynamicReadPermissionMixin, View):
    """Export active students in a program as an Excel (.xlsx) file."""

    section = "programs"

    def get(self, request, pk):
        from io import BytesIO

        from django.http import HttpResponse
        from openpyxl import Workbook

        program = get_object_or_404(Program, pk=pk)

        enrollments = (
            Enrollment.objects.filter(
                program=program, active=True, student__graduated=False
            )
            .select_related("student")
            .annotate(
                sort_first=Lower(
                    Coalesce(
                        NullIf("student__first_name", Value("")),
                        "student__legal_first_name",
                    )
                ),
                sort_last=Lower("student__last_name"),
            )
            .order_by("sort_first", "sort_last")
        )

        wb = Workbook()
        ws = wb.active
        ws.title = "Students"
        ws.append(["First Name", "Last Name", "Grade"])

        for enrollment in enrollments:
            student = enrollment.student
            ws.append(
                [
                    student.first_name or student.legal_first_name,
                    student.last_name,
                    student.grade_display or "",
                ]
            )

        buffer = BytesIO()
        wb.save(buffer)
        buffer.seek(0)

        filename = f"{program.name} - Students.xlsx"
        response = HttpResponse(
            buffer.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class ProgramDocumentCreateView(
    LogFormSaveMixin,
    LoginRequiredMixin,
    PermissionRequiredMixin,
    CreateView,
):
    """Add a blank document (e.g. PDF) to a Program. Shown on the Program
    settings page so lead mentors can manage them without going through
    the Django admin.
    """

    permission_required = "programs.change_program"
    model = ProgramDocument
    form_class = ProgramDocumentForm
    template_name = "programs/program_document_form.html"

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
        ctx["is_create"] = True
        return ctx

    def form_valid(self, form):
        messages.success(self.request, "Document added.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("program_detail", args=[self.program.pk])


class ProgramDocumentUpdateView(
    LogFormSaveMixin,
    LoginRequiredMixin,
    PermissionRequiredMixin,
    UpdateView,
):
    permission_required = "programs.change_program"
    model = ProgramDocument
    form_class = ProgramDocumentForm
    template_name = "programs/program_document_form.html"
    pk_url_kwarg = "doc_id"

    def dispatch(self, request, *args, **kwargs):
        self.program = get_object_or_404(Program, pk=kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_object(self, queryset=None):
        return get_object_or_404(
            ProgramDocument, pk=self.kwargs["doc_id"], program=self.program
        )

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
        messages.success(self.request, "Document updated.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("program_detail", args=[self.program.pk])


class ProgramDocumentDeleteView(
    LoginRequiredMixin,
    PermissionRequiredMixin,
    View,
):
    """Delete a Program Document. POST-only (with a JS confirm on the
    detail page); GET renders a small confirmation page for safety.
    """

    permission_required = "programs.change_program"
    template_name = "programs/program_document_confirm_delete.html"

    def dispatch(self, request, *args, **kwargs):
        self.program = get_object_or_404(Program, pk=kwargs["pk"])
        self.document = get_object_or_404(
            ProgramDocument, pk=kwargs["doc_id"], program=self.program
        )
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        return render(
            request,
            self.template_name,
            {"program": self.program, "document": self.document},
        )

    def post(self, request, *args, **kwargs):
        name = self.document.name
        self.document.delete()
        messages.success(request, f"Deleted document \u201c{name}\u201d.")
        return redirect("program_detail", pk=self.program.pk)
