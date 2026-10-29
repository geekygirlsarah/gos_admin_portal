"""Monthly balance update service for students and parents."""

import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from django.utils import timezone

from programs.models import Enrollment, Student
from programs.utils import active_students, get_student_balance_data


def collect_monthly_balances_data(
    student: Student,
    as_of_date: Optional[datetime.date] = None,
    days_back: int = 30,
    include_paid_off: bool = False,
) -> Dict[str, Any]:
    """Collect itemized fees, sliding scale, payments, and balance across all active programs for a student.

    If ``include_paid_off`` is False, programs that are fully paid off ($0 balance) and have no
    transactions within the past ``days_back`` days are excluded.
    """
    as_of = as_of_date or timezone.localdate()
    cutoff_date = as_of - datetime.timedelta(days=days_back)

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

        # Check for recent transactions within the days_back window
        has_recent_transactions = False
        for entry in b_data["entries"]:
            entry_date = entry.get("date")
            if isinstance(entry_date, datetime.datetime):
                entry_date = entry_date.date()
            if entry_date and entry_date >= cutoff_date:
                has_recent_transactions = True
                break

        # Exclude paid off programs with no recent activity unless include_paid_off is requested
        if (
            not include_paid_off
            and prog_balance == Decimal("0.00")
            and not has_recent_transactions
        ):
            continue

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
                "has_recent_transactions": has_recent_transactions,
            }
        )

    has_recent_activity = any(pb["has_recent_transactions"] for pb in program_balances)
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
        "has_recent_activity": has_recent_activity,
    }


def _get_student_parent_emails(student: Student) -> List[tuple[Any, str]]:
    """Resolve opted-in or fallback parent notification emails for a student."""
    parents = student.all_parents
    opted_in = [
        p for p in parents if p.email_updates and getattr(p, "login_enabled", True)
    ]
    if not opted_in:
        fallback = student.primary_contact or (parents[0] if parents else None)
        if fallback and getattr(fallback, "login_enabled", True):
            opted_in = [fallback]

    parent_emails = []
    for p in opted_in:
        email = p.personal_email or p.andrew_email or (p.user.email if p.user else None)
        if email and email.strip():
            parent_emails.append((p, email.strip()))

    return parent_emails


def get_monthly_balance_recipients(
    positive_only: bool = True,
    student_id: Optional[int] = None,
    as_of_date: Optional[datetime.date] = None,
    days_back: int = 30,
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
    include_paid_off = not positive_only
    for st in active_student_qs:
        data = collect_monthly_balances_data(
            st,
            as_of_date=as_of_date,
            days_back=days_back,
            include_paid_off=include_paid_off,
        )
        if positive_only:
            if (
                data["total_balance"] <= Decimal("0.00")
                and not data["has_recent_activity"]
            ):
                continue
            if not data["program_balances"]:
                continue
        elif not data["program_balances"] and not data["has_balance"]:
            continue

        parent_emails = _get_student_parent_emails(st)
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
