from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView


def _request_organization(request):
    organization = getattr(request, "organization", None)
    if organization is None:
        raise Http404
    return organization


def _organization_profile(user, name, organization):
    profile = getattr(user, name, None)
    if profile and profile.organization_id == organization.pk:
        return profile
    return None


def _organization_enrollments(organization):
    from programs.models import Enrollment

    return Enrollment.objects.for_organization(organization).filter(
        student__organization=organization, program__organization=organization
    )


class MyProfileView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        user = request.user
        org = _request_organization(request)
        student = _organization_profile(user, "student_profile", org)
        if student:
            return redirect("student_detail", pk=student.pk)

        adult = _organization_profile(user, "adult_profile", org)
        if adult:
            return redirect("adult_detail", pk=adult.pk)

        # Fallback to dashboard if no profile found (e.g. superuser without profile)
        return redirect("profile_dashboard")


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "portal/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        org = _request_organization(self.request)

        # Pending (in-progress) applications tied to this user by email,
        # shown in the dashboard's "Pending Applications" box.
        from applications.services import applications_for_user

        context["pending_applications"] = applications_for_user(user, organization=org)

        # ── Role detection & multi-role list ──────────────────────────────────
        from programs.permission_views import user_is_lead_mentor

        is_lead_mentor = user_is_lead_mentor(user, org=org)
        context["is_lead_mentor"] = is_lead_mentor

        student = _organization_profile(user, "student_profile", org)
        adult = _organization_profile(user, "adult_profile", org)

        user_roles = []
        if is_lead_mentor:
            user_roles.append(
                {
                    "id": "lead-mentor",
                    "name": "Lead Mentor",
                    "badge_class": "bg-primary text-white",
                    "icon": "bi-shield-check",
                }
            )
        if adult and getattr(adult, "is_mentor", False):
            user_roles.append(
                {
                    "id": "mentor",
                    "name": "Mentor",
                    "badge_class": "bg-info text-dark",
                    "icon": "bi-person-badge",
                }
            )
        elif is_lead_mentor and not (adult and getattr(adult, "is_mentor", False)):
            user_roles.append(
                {
                    "id": "mentor",
                    "name": "Mentor",
                    "badge_class": "bg-info text-dark",
                    "icon": "bi-person-badge",
                }
            )
        if adult and getattr(adult, "is_parent", False):
            user_roles.append(
                {
                    "id": "parent",
                    "name": "Parent",
                    "badge_class": "bg-success text-white",
                    "icon": "bi-people",
                }
            )
        if student:
            user_roles.append(
                {
                    "id": "student",
                    "name": "Student",
                    "badge_class": "bg-secondary text-white",
                    "icon": "bi-mortarboard",
                }
            )
        if adult and getattr(adult, "is_alumni", False):
            user_roles.append(
                {
                    "id": "alumni",
                    "name": "Alumni",
                    "badge_class": "bg-dark text-white",
                    "icon": "bi-award",
                }
            )

        context["user_roles"] = user_roles
        context["has_multiple_roles"] = len(user_roles) > 1

        # ── Lead Mentor Command Center Stats ─────────────────────────────────
        if is_lead_mentor:
            from applications.models import Application
            from orders.models import OrderItem
            from programs.models import EmailDeliveryEvent, Program, SlidingScale
            from programs.utils import students_in_running_programs

            active_programs_count = (
                Program.objects.for_organization(org).filter(active=True).count()
            )
            active_students_count = students_in_running_programs(org).count()
            pending_apps_count = (
                Application.objects.for_organization(org)
                .filter(status=Application.Status.SUBMITTED)
                .count()
            )
            pending_orders_count = (
                OrderItem.objects.for_organization(org)
                .filter(order__isnull=True)
                .count()
            )
            pending_sliding_count = (
                SlidingScale.objects.for_organization(org)
                .filter(status=SlidingScale.STATUS_PENDING)
                .count()
            )
            pending_email_issues_count = (
                EmailDeliveryEvent.objects.verified_for_organization(org)
                .filter(
                    resolved=False,
                    event_type__in=[
                        EmailDeliveryEvent.EVENT_BOUNCED,
                        EmailDeliveryEvent.EVENT_COMPLAINT,
                    ],
                )
                .count()
            )

            orders_program = (
                Program.objects.for_organization(
                    getattr(self.request, "organization", None)
                )
                .filter(active=True, features__key="orders")
                .first()
                or Program.objects.for_organization(
                    getattr(self.request, "organization", None)
                )
                .filter(active=True)
                .first()
            )

            context["lead_stats"] = {
                "active_programs_count": active_programs_count,
                "active_students_count": active_students_count,
                "pending_applications_count": pending_apps_count,
                "pending_orders_count": pending_orders_count,
                "pending_sliding_count": pending_sliding_count,
                "pending_email_issues_count": pending_email_issues_count,
                "orders_program_pk": (orders_program.pk if orders_program else None),
            }

        # ── Student profile ──────────────────────────────────────────────────
        context["student"] = student
        if student:
            enrollments = (
                _organization_enrollments(org)
                .filter(student=student)
                .select_related("program", "team", "crew", "subteam")
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
                    e.has_orders = e.program.has_feature("orders")
                    e.has_travel = e.program.has_feature("travel")
                    if e.has_attendance:
                        e.attendance_stats = get_student_attendance_stats(
                            student, e.program
                        )
                    if e.has_outreach:
                        from outreach.utils import get_student_outreach_stats

                        e.outreach_stats = get_student_outreach_stats(
                            student, e.program
                        )
                    if e.has_travel:
                        from travel.utils import get_student_travel_highlights

                        e.travel_highlights = get_student_travel_highlights(
                            student, e.program
                        )

                    active_enrollments.append(e)
                else:
                    other_enrollments.append(e)

            context["active_enrollments"] = active_enrollments
            context["other_enrollments"] = other_enrollments
            context["student_enrollments"] = enrollments  # Keep for compatibility

        # ── Adult profile (parent / mentor / alumni) ─────────────────────────
        # ── Badges (student + parent visibility) ─────────────────────────────
        # Only show badges if the student has been enrolled in at least one
        # program with the badges feature enabled.
        context["has_badges"] = False
        try:
            from badges.models import StudentBadge
        except ImportError:
            StudentBadge = None
        linked_students = []
        if adult and adult.is_parent:
            from programs.models import AdultStudentRelationship

            relationships = (
                AdultStudentRelationship.objects.for_organization(org)
                .filter(adult=adult, student__organization=org)
                .select_related("student")
                .order_by("student__last_name", "student__legal_first_name")
            )
            for relationship in relationships:
                linked_student = relationship.student
                linked_student.attached_rel = relationship.relationship_to_student
                linked_students.append(linked_student)
        if StudentBadge:
            if student:
                has_badges_feature = (
                    _organization_enrollments(org)
                    .filter(
                        student=student,
                        active=True,
                        program__features__key="badges",
                        program__features__organization=org,
                    )
                    .exists()
                )
                context["has_badges"] = has_badges_feature
                if has_badges_feature:
                    context["badges_earned"] = (
                        StudentBadge.objects.for_organization(org)
                        .filter(student=student, badge__organization=org)
                        .select_related("badge", "awarded_by")
                    )
                    context["badges_count"] = context["badges_earned"].count()
            elif adult and getattr(adult, "is_parent", False):
                # Check if any linked student has badges enabled
                parent_has_badges = (
                    _organization_enrollments(org)
                    .filter(
                        student__in=linked_students,
                        active=True,
                        program__features__key="badges",
                        program__features__organization=org,
                    )
                    .exists()
                )
                context["has_badges"] = parent_has_badges
                if parent_has_badges:
                    context["badges_by_student"] = {
                        s: list(
                            StudentBadge.objects.for_organization(org)
                            .filter(student=s, badge__organization=org)
                            .select_related("badge", "awarded_by")
                        )
                        for s in linked_students
                    }
        context["adult"] = adult
        if adult:
            context["is_parent"] = adult.is_parent
            context["is_mentor"] = adult.is_mentor
            context["is_alumni"] = adult.is_alumni

            if adult.is_parent:
                # Attach per-program balance info to each student
                parent_data = []
                from attendance.services import get_student_attendance_stats
                from programs.permission_views import can_user_read
                from programs.utils.balances import (
                    InconsistentBalanceStatementError,
                    TenantBalanceStatementCalculator,
                )

                parent_enrollments = list(
                    _organization_enrollments(org)
                    .filter(student_id__in=[s.pk for s in linked_students])
                    .select_related("student", "program", "team", "crew", "subteam")
                    .order_by("-program__start_date")
                )
                enrollments_by_student = {}
                programs_by_id = {}
                for enrollment in parent_enrollments:
                    enrollments_by_student.setdefault(enrollment.student_id, []).append(
                        enrollment
                    )
                    programs_by_id[enrollment.program_id] = enrollment.program
                balance_calculator = TenantBalanceStatementCalculator(
                    org, linked_students, programs_by_id.values()
                )
                can_view_sliding = can_user_read(
                    self.request.user, "sliding_scale", org=org
                )

                for s in linked_students:
                    enrollments = enrollments_by_student.get(s.pk, [])
                    active_rows = []
                    other_rows = []
                    for e in enrollments:
                        try:
                            balance = balance_calculator.get_balance_data(
                                s, e.program, can_view_sliding=can_view_sliding
                            )["balance"]
                            balance_unavailable = False
                        except InconsistentBalanceStatementError:
                            balance = None
                            balance_unavailable = True

                        # Add attendance/outreach info
                        e.has_attendance = e.program.has_feature("attendance")
                        e.has_outreach = e.program.has_feature("outreach")
                        e.has_orders = e.program.has_feature("orders")
                        e.has_travel = e.program.has_feature("travel")
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

                        if e.has_travel:
                            from travel.utils import get_parent_travel_highlights

                            highlights, pending = get_parent_travel_highlights(
                                s, e.program
                            )
                            e.travel_highlights = highlights
                            e.travel_pending = pending

                        row = {
                            "enrollment": e,
                            "balance": balance,
                            "balance_unavailable": balance_unavailable,
                        }
                        if e.program.status == "Active" and e.active:
                            active_rows.append(row)
                        else:
                            other_rows.append(row)
                    # ── Badges per student (for parent dashboard box) ──────────────
                    badges_for_student = []
                    has_badges_for_student = False
                    if StudentBadge is not None:
                        has_badges_for_student = (
                            _organization_enrollments(org)
                            .filter(
                                student=s,
                                active=True,
                                program__features__key="badges",
                                program__features__organization=org,
                            )
                            .exists()
                        )
                        if has_badges_for_student:
                            badges_for_student = list(
                                StudentBadge.objects.for_organization(org)
                                .filter(student=s, badge__organization=org)
                                .select_related("badge", "awarded_by")
                            )
                    parent_data.append(
                        {
                            "student": s,
                            "active_rows": active_rows,
                            "other_rows": other_rows,
                            "program_rows": active_rows
                            + other_rows,  # Keep for compatibility
                            "has_badges": has_badges_for_student,
                            "badges": badges_for_student,
                            "badges_count": len(badges_for_student),
                        }
                    )
                context["parent_data"] = parent_data
                family_balance_unavailable = any(
                    row["balance_unavailable"]
                    for entry in parent_data
                    for row in entry["active_rows"]
                )
                family_total_owed = (
                    None
                    if family_balance_unavailable
                    else sum(
                        max(Decimal("0"), row["balance"])
                        for entry in parent_data
                        for row in entry["active_rows"]
                    )
                )
                context["family_balance_unavailable"] = family_balance_unavailable
                context["family_total_owed"] = family_total_owed

            if adult.is_mentor or is_lead_mentor:
                from programs.models import Program

                # Get all programs that are currently Active or Upcoming so
                # mentors can prepare (rosters, emails) before a program starts.
                all_active = (
                    Program.objects.for_organization(
                        getattr(self.request, "organization", None)
                    )
                    .filter(active=True)
                    .order_by("name")
                )
                mentor_active_programs = [
                    p for p in all_active if p.status in ("Active", "Upcoming")
                ]
                context["mentor_active_programs"] = mentor_active_programs

                if adult and adult.is_mentor:
                    # Upcoming outreach shifts this mentor volunteered to support.
                    from outreach.models import OutreachMentorSignup

                    mentor_signups = (
                        OutreachMentorSignup.objects.filter(adult=adult)
                        .select_related(
                            "shift", "shift__event", "shift__event__program"
                        )
                        .order_by("shift__date", "shift__start_time")
                    )
                    context["mentor_outreach_signups"] = [
                        s for s in mentor_signups if not s.shift.is_past
                    ]

                    # Upcoming trips this mentor is going on.
                    from travel.utils import get_mentor_travel_signups

                    context["mentor_travel_signups"] = get_mentor_travel_signups(adult)

            if adult.is_alumni:
                student_record = adult.student_record
                if student_record and student_record.organization_id != org.pk:
                    student_record = None
                context["student_record"] = student_record
                context["enrollments"] = []
                if student_record:
                    context["enrollments"] = (
                        _organization_enrollments(org)
                        .filter(student=student_record)
                        .select_related("program", "team")
                    )

        elif is_lead_mentor:
            from programs.models import Program

            all_active = (
                Program.objects.for_organization(
                    getattr(self.request, "organization", None)
                )
                .filter(active=True)
                .order_by("name")
            )
            mentor_active_programs = [
                p for p in all_active if p.status in ("Active", "Upcoming")
            ]
            context["mentor_active_programs"] = mentor_active_programs

        return context


class ParentPaymentsAccessMixin(LoginRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        from programs.permission_views import user_is_parent

        organization = _request_organization(request)
        self.parent_adult = _organization_profile(
            request.user, "adult_profile", organization
        )
        # Check the parent flag directly rather than the role string so a
        # parent who also mentors (or is an alumni) still reaches Payments.
        if (
            not self.parent_adult
            or not self.parent_adult.is_parent
            or not user_is_parent(request.user, org=organization)
        ):
            messages.error(
                request, "You do not have permission to access that section."
            )
            return redirect("home")

        return super().dispatch(request, *args, **kwargs)


class ParentPaymentsView(ParentPaymentsAccessMixin, View):
    template_name = "parents/payments.html"

    def get(self, request, *args, **kwargs):
        from programs.models import AdultStudentRelationship, SlidingScale
        from programs.permission_views import can_user_read
        from programs.utils.balances import (
            InconsistentBalanceStatementError,
            TenantBalanceStatementCalculator,
        )

        organization = _request_organization(request)
        relationships = (
            AdultStudentRelationship.objects.for_organization(organization)
            .filter(
                organization=organization,
                adult=self.parent_adult,
                adult__organization=organization,
                student__organization=organization,
            )
            .select_related("student")
        )
        students = {
            relationship.student_id: relationship.student
            for relationship in relationships
        }
        enrollments = list(
            _organization_enrollments(organization)
            .filter(student_id__in=students)
            .select_related("student", "program")
            .order_by(
                "student__last_name", "student__preferred_first_name", "program__name"
            )
        )
        programs = {
            enrollment.program_id: enrollment.program for enrollment in enrollments
        }
        balance_calculator = TenantBalanceStatementCalculator(
            organization, students.values(), programs.values()
        )
        can_view_sliding = can_user_read(
            request.user, "sliding_scale", org=organization
        )

        student_rows_by_id = {}
        today = timezone.localdate()
        for enrollment in enrollments:
            student_data = student_rows_by_id.setdefault(
                enrollment.student_id,
                {
                    "student": enrollment.student,
                    "program_rows": [],
                    "student_total_owed": Decimal("0"),
                    "balance_unavailable": False,
                    "sliding_scale": None,
                },
            )
            try:
                balance_data = balance_calculator.get_balance_data(
                    enrollment.student,
                    enrollment.program,
                    can_view_sliding=can_view_sliding,
                )
                balance = balance_data["balance"]
                amount_owed = max(balance, 0)
                if student_data["sliding_scale"] is None:
                    student_data["sliding_scale"] = next(
                        (
                            scale
                            for scale in balance_data["all_overlapping_sliding_scales"]
                            if (scale.date is None or scale.date <= today)
                            and (
                                scale.expiration_date is None
                                or scale.expiration_date >= today
                            )
                        ),
                        None,
                    )
                balance_unavailable = False
                student_data["student_total_owed"] += amount_owed
            except InconsistentBalanceStatementError:
                balance = None
                amount_owed = None
                balance_unavailable = True
                student_data["balance_unavailable"] = True
            student_data["program_rows"].append(
                {
                    "program": enrollment.program,
                    "balance": balance,
                    "amount_owed": amount_owed,
                    "balance_unavailable": balance_unavailable,
                    "balance_url": reverse(
                        "program_student_balance",
                        args=[enrollment.program_id, enrollment.student_id],
                    ),
                }
            )

        if can_view_sliding:
            pending_applications = (
                SlidingScale.objects.for_organization(organization)
                .filter(
                    organization=organization,
                    student_id__in=student_rows_by_id,
                    student__organization=organization,
                    status=SlidingScale.STATUS_PENDING,
                )
                .select_related("student")
                .order_by("-created_at")
            )
            pending_by_student = {}
            for application in pending_applications:
                pending_by_student.setdefault(application.student_id, application)

            for student_data in student_rows_by_id.values():
                if student_data["balance_unavailable"]:
                    student_data["student_total_owed"] = None
                pending_application = pending_by_student.get(student_data["student"].pk)
                student_data["sliding_scale_pending"] = pending_application
                student_data["sliding_scale_apply_url"] = reverse(
                    "sliding_scale_apply", args=[student_data["student"].pk]
                )
                if pending_application:
                    student_data["sliding_scale_withdraw_url"] = reverse(
                        "sliding_scale_withdraw", args=[pending_application.pk]
                    )
        else:
            for student_data in student_rows_by_id.values():
                if student_data["balance_unavailable"]:
                    student_data["student_total_owed"] = None

        student_rows = sorted(
            student_rows_by_id.values(),
            key=lambda row: (
                (row["student"].last_name or "").lower(),
                (
                    row["student"].preferred_first_name
                    or row["student"].legal_first_name
                    or ""
                ).lower(),
            ),
        )
        family_balance_unavailable = any(
            row["balance_unavailable"] for row in student_rows
        )
        grand_total = (
            None
            if family_balance_unavailable
            else sum(
                (row["student_total_owed"] for row in student_rows),
                start=Decimal("0"),
            )
        )
        from organizations.utils import get_organization_payment_settings

        payment_settings = get_organization_payment_settings(
            _request_organization(request)
        )

        return render(
            request,
            self.template_name,
            {
                "student_rows": student_rows,
                "grand_total": grand_total,
                "family_balance_unavailable": family_balance_unavailable,
                "payment_instructions": payment_settings["instructions"],
                "online_payment_url": payment_settings["online_payment_url"],
            },
        )
