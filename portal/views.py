from decimal import Decimal

from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView


def _compute_student_program_balance(student, program):
    """Return the net balance owed (fees - payments - sliding scale discount)."""
    from programs.models import FeeAssignment, Payment, SlidingScale

    # Fees assigned to this student (or all fees if no per-student assignments exist)
    assigned_fee_ids = FeeAssignment.objects.filter(
        fee__program=program, student=student
    ).values_list("fee_id", flat=True)
    fees_qs = program.fees.all()
    if assigned_fee_ids:
        fees_qs = fees_qs.filter(pk__in=assigned_fee_ids)
    total_fees = sum(f.amount for f in fees_qs) or Decimal("0.00")

    # Sliding scale discount
    try:
        scale = SlidingScale.objects.get(student=student, program=program)
        discount = (scale.percent / Decimal("100")) * total_fees
    except SlidingScale.DoesNotExist:
        discount = Decimal("0.00")

    # Payments made
    total_paid = (
        sum(
            p.amount
            for p in Payment.objects.filter(student=student, program=program)
        )
        or Decimal("0.00")
    )

    return total_fees - discount - total_paid


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "portal/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user

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
            context["student_enrollments"] = enrollments

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
                for s in linked_students:
                    enrollments = (
                        Enrollment.objects.filter(student=s)
                        .select_related("program")
                        .order_by("-program__start_date")
                    )
                    program_rows = []
                    for e in enrollments:
                        balance = _compute_student_program_balance(s, e.program)
                        program_rows.append({"enrollment": e, "balance": balance})
                    parent_data.append(
                        {
                            "student": s,
                            "program_rows": program_rows,
                        }
                    )
                context["parent_data"] = parent_data

            if adult.is_alumni:
                from programs.models import Enrollment

                context["student_record"] = adult.student_record
                if adult.student_record:
                    context["enrollments"] = Enrollment.objects.filter(
                        student=adult.student_record
                    ).select_related("program", "team")

        return context
