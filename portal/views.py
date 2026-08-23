from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView


class MyProfileView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        user = request.user
        student = getattr(user, "student_profile", None)
        if student:
            return redirect("student_detail", pk=student.pk)

        adult = getattr(user, "adult_profile", None)
        if adult:
            return redirect("adult_detail", pk=adult.pk)

        # Fallback to dashboard if no profile found (e.g. superuser without profile)
        return redirect("profile_dashboard")


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "portal/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user

        # Pending (in-progress) applications tied to this user by email,
        # shown in the dashboard's "Pending Applications" box.
        from applications.services import applications_for_user

        context["pending_applications"] = applications_for_user(user)

        # ── Student profile ──────────────────────────────────────────────────
        student = getattr(user, "student_profile", None)
        context["student"] = student
        if student:
            from programs.models import Enrollment

            enrollments = (
                Enrollment.objects.filter(student=student)
                .select_related("program")
                .order_by("-program__start_date")
            )
            # Separate active from non-active programs for a cleaner dashboard
            active_enrollments = []
            other_enrollments = []
            from attendance.services import get_student_attendance_stats

            for e in enrollments:
                if e.program.status == "Active" and e.active:
                    e.has_attendance = e.program.has_feature("attendance")
                    e.has_outreach = e.program.has_feature("outreach")
                    if e.has_attendance:
                        e.attendance_stats = get_student_attendance_stats(
                            student, e.program
                        )
                    if e.has_outreach:
                        from outreach.utils import get_student_outreach_stats

                        e.outreach_stats = get_student_outreach_stats(
                            student, e.program
                        )

                    active_enrollments.append(e)
                else:
                    other_enrollments.append(e)

            context["active_enrollments"] = active_enrollments
            context["other_enrollments"] = other_enrollments
            context["student_enrollments"] = enrollments  # Keep for compatibility

        # ── Adult profile (parent / mentor / alumni) ─────────────────────────
        adult = getattr(user, "adult_profile", None)
        context["adult"] = adult
        if adult:
            context["is_parent"] = adult.is_parent
            context["is_mentor"] = adult.is_mentor
            context["is_alumni"] = adult.is_alumni

            if adult.is_parent:
                from programs.models import Enrollment

                linked_students = adult.all_students()
                # Attach per-program balance info to each student
                parent_data = []
                from attendance.services import get_student_attendance_stats

                for s in linked_students:
                    enrollments = (
                        Enrollment.objects.filter(student=s)
                        .select_related("program")
                        .order_by("-program__start_date")
                    )
                    active_rows = []
                    other_rows = []
                    for e in enrollments:
                        from programs.permission_views import can_user_read
                        from programs.utils import get_student_program_balance

                        can_view_sliding = can_user_read(
                            self.request.user, "sliding_scale"
                        )
                        balance = get_student_program_balance(
                            s, e.program, can_view_sliding=can_view_sliding
                        )

                        # Add attendance/outreach info
                        e.has_attendance = e.program.has_feature("attendance")
                        e.has_outreach = e.program.has_feature("outreach")
                        if e.has_attendance:
                            e.attendance_stats = get_student_attendance_stats(
                                s, e.program
                            )
                        if e.has_outreach:
                            from outreach.models import OutreachEvent, OutreachSignup

                            today = timezone.now().date()
                            outreach_events = OutreachEvent.objects.filter(
                                program=e.program
                            ).prefetch_related("shifts")
                            upcoming_events = sorted(
                                (
                                    ev
                                    for ev in outreach_events
                                    if ev.start_date and ev.start_date >= today
                                ),
                                key=lambda ev: (ev.start_date, ev.start_time),
                            )
                            # Show events this child already signed up for in
                            # full, plus a couple of others to encourage
                            # signing up (per program, so it stays capped even
                            # with many programs/events).
                            signed_up_event_ids = set(
                                OutreachSignup.objects.filter(
                                    student=s, shift__event__program=e.program
                                ).values_list("shift__event_id", flat=True)
                            )
                            signed_up = [
                                ev
                                for ev in upcoming_events
                                if ev.id in signed_up_event_ids
                            ]
                            suggested = [
                                ev
                                for ev in upcoming_events
                                if ev.id not in signed_up_event_ids
                            ][:2]
                            for event in signed_up:
                                event.user_signup = True
                            for event in suggested:
                                event.user_signup = False
                            e.outreach_highlights = signed_up + suggested

                        row = {"enrollment": e, "balance": balance}
                        if e.program.status == "Active" and e.active:
                            active_rows.append(row)
                        else:
                            other_rows.append(row)
                    parent_data.append(
                        {
                            "student": s,
                            "active_rows": active_rows,
                            "other_rows": other_rows,
                            "program_rows": active_rows
                            + other_rows,  # Keep for compatibility
                        }
                    )
                context["parent_data"] = parent_data

            if adult.is_mentor:
                from programs.models import Program

                # Get all programs that are currently Active or Upcoming so
                # mentors can prepare (rosters, emails) before a program starts.
                all_active = Program.objects.filter(active=True).order_by("name")
                mentor_active_programs = [
                    p for p in all_active if p.status in ("Active", "Upcoming")
                ]
                context["mentor_active_programs"] = mentor_active_programs

                # Upcoming outreach shifts this mentor volunteered to support.
                from outreach.models import OutreachMentorSignup

                mentor_signups = (
                    OutreachMentorSignup.objects.filter(adult=adult)
                    .select_related("shift", "shift__event", "shift__event__program")
                    .order_by("shift__date", "shift__start_time")
                )
                context["mentor_outreach_signups"] = [
                    s for s in mentor_signups if not s.shift.is_past
                ]

            if adult.is_alumni:
                from programs.models import Enrollment

                context["student_record"] = adult.student_record
                if adult.student_record:
                    context["enrollments"] = Enrollment.objects.filter(
                        student=adult.student_record
                    ).select_related("program", "team")

        return context


class ParentPaymentsAccessMixin(LoginRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        from programs.permission_views import user_is_parent

        # Check the parent flag directly rather than the role string so a
        # parent who also mentors (or is an alumni) still reaches Payments.
        if not user_is_parent(request.user):
            messages.error(
                request, "You do not have permission to access that section."
            )
            return redirect("home")

        self.parent_adult = getattr(request.user, "adult_profile", None)
        if not self.parent_adult or not self.parent_adult.is_parent:
            messages.error(
                request, "You do not have permission to access that section."
            )
            return redirect("home")

        return super().dispatch(request, *args, **kwargs)


class ParentPaymentsView(ParentPaymentsAccessMixin, View):
    template_name = "parents/payments.html"
    online_payment_url = getattr(
        settings,
        "PARENT_PAYMENTS_ONLINE_PORTAL_URL",
        "https://commerce.cashnet.com/CMU267",
    )

    def get(self, request, *args, **kwargs):
        from programs.models import Enrollment, SlidingScale
        from programs.permission_views import can_user_read
        from programs.utils import get_active_sliding_scale, get_student_program_balance

        can_view_sliding = can_user_read(request.user, "sliding_scale")
        enrollments = (
            Enrollment.objects.filter(student__in=self.parent_adult.all_students())
            .select_related("student", "program")
            .order_by("student__last_name", "student__first_name", "program__name")
        )

        students = {}
        for enrollment in enrollments:
            balance = get_student_program_balance(
                enrollment.student,
                enrollment.program,
                can_view_sliding=can_view_sliding,
            )
            student_data = students.setdefault(
                enrollment.student_id,
                {
                    "student": enrollment.student,
                    "program_rows": [],
                    "student_total_owed": Decimal("0"),
                },
            )
            amount_owed = max(balance, 0)
            student_data["student_total_owed"] += amount_owed
            student_data["program_rows"].append(
                {
                    "program": enrollment.program,
                    "balance": balance,
                    "amount_owed": amount_owed,
                    "balance_url": reverse(
                        "program_student_balance",
                        args=[enrollment.program_id, enrollment.student_id],
                    ),
                }
            )

        if can_view_sliding:
            for student_data in students.values():
                student = student_data["student"]
                active = get_active_sliding_scale(student)
                pending_application = (
                    SlidingScale.objects.filter(
                        student=student, status=SlidingScale.STATUS_PENDING
                    )
                    .order_by("-created_at")
                    .first()
                )
                student_data["sliding_scale"] = active
                student_data["sliding_scale_pending"] = pending_application
                student_data["sliding_scale_apply_url"] = reverse(
                    "sliding_scale_apply", args=[student.pk]
                )
                if pending_application:
                    student_data["sliding_scale_withdraw_url"] = reverse(
                        "sliding_scale_withdraw", args=[pending_application.pk]
                    )

        student_rows = sorted(
            students.values(),
            key=lambda row: (
                (row["student"].last_name or "").lower(),
                (
                    row["student"].first_name or row["student"].legal_first_name or ""
                ).lower(),
            ),
        )
        grand_total = sum(
            (row["student_total_owed"] for row in student_rows),
            start=Decimal("0"),
        )

        return render(
            request,
            self.template_name,
            {
                "student_rows": student_rows,
                "grand_total": grand_total,
                "online_payment_url": self.online_payment_url,
            },
        )
