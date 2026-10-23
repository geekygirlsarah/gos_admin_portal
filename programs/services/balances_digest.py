"""Monthly balance update service for students and parents."""

from decimal import Decimal
from typing import Any, Dict, List, Optional

from programs.models import Enrollment, Student
from programs.utils import active_students, get_student_balance_data


def collect_monthly_balances_data(student: Student) -> Dict[str, Any]:
    """Collect itemized fees, sliding scale, payments, and balance across all active programs for a student."""
    active_enrollments = list(
        Enrollment.objects.filter(
            student=student, active=True, program__active=True
        ).select_related("program")
    )

    program_balances = []
    total_balance = Decimal("0.00")
    total_fees_all = Decimal("0.00")
    total_payments_all = Decimal("0.00")
    total_sliding_all = Decimal("0.00")

    for e in active_enrollments:
        b_data = get_student_balance_data(student, e.program, can_view_sliding=True)
        prog_balance = b_data["balance"]
        total_balance += prog_balance
        total_fees_all += b_data["total_fees"]
        total_payments_all += b_data["total_payments"]
        total_sliding_all += b_data["total_sliding"]

        program_balances.append(
            {
                "program": e.program,
                "entries": b_data["entries"],
                "total_fees": b_data["total_fees"],
                "total_sliding": b_data["total_sliding"],
                "total_payments": b_data["total_payments"],
                "balance": prog_balance,
                "sliding_scale": b_data["sliding_scale"],
            }
        )

    has_balance = bool(
        total_balance != Decimal("0.00")
        or any(pb["total_fees"] > Decimal("0.00") for pb in program_balances)
    )

    return {
        "student": student,
        "program_balances": program_balances,
        "total_balance": total_balance,
        "total_fees": total_fees_all,
        "total_payments": total_payments_all,
        "total_sliding": total_sliding_all,
        "has_balance": has_balance,
    }


def get_monthly_balance_recipients(
    positive_only: bool = True,
    student_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Determine all recipient targets for monthly balance statements."""
    active_student_qs = (
        active_students()
        .filter(enrollment__active=True, enrollment__program__active=True)
        .distinct()
    )

    if student_id:
        active_student_qs = active_student_qs.filter(pk=student_id)

    targets = []
    for st in active_student_qs:
        data = collect_monthly_balances_data(st)
        if positive_only and data["total_balance"] <= Decimal("0.00"):
            continue

        parents = st.all_parents
        opted_in = [
            p for p in parents if p.email_updates and getattr(p, "login_enabled", True)
        ]
        if not opted_in:
            fallback = st.primary_contact or (parents[0] if parents else None)
            if fallback and getattr(fallback, "login_enabled", True):
                opted_in = [fallback]

        parent_emails = []
        for p in opted_in:
            email = (
                p.personal_email or p.andrew_email or (p.user.email if p.user else None)
            )
            if email and email.strip():
                parent_emails.append((p, email.strip()))

        for adult, email in parent_emails:
            targets.append(
                {
                    "student": st,
                    "adult": adult,
                    "email": email,
                    "data": data,
                }
            )

    return targets
