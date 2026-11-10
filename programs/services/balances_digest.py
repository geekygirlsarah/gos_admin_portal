"""Monthly balance update service for students and parents."""

import datetime
import logging
from collections import defaultdict
from decimal import Decimal
from typing import Any, Dict, List, Optional

from django.utils import timezone

from programs.models import AdultStudentRelationship, Enrollment, Student
from programs.utils.balances import (
    InconsistentBalanceStatementError,
    TenantBalanceStatementCalculator,
)

logger = logging.getLogger(__name__)


def _scoped_active_enrollments(organization, students):
    enrollments = defaultdict(list)
    rows = Enrollment.objects.filter(
        student_id__in=[student.pk for student in students],
        student__organization=organization,
        active=True,
        program__active=True,
    ).select_related("program")
    for enrollment in rows:
        if (
            enrollment.organization_id != organization.pk
            or enrollment.program.organization_id != organization.pk
        ):
            logger.warning(
                "Monthly balance enrollment skipped due to inconsistent ownership."
            )
            continue
        enrollments[enrollment.student_id].append(enrollment)
    return enrollments


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
    if not student.organization_id:
        raise InconsistentBalanceStatementError
    organization = student.organization
    active_enrollments = _scoped_active_enrollments(organization, [student])[student.pk]
    calculator = TenantBalanceStatementCalculator(
        organization,
        [student],
        [enrollment.program for enrollment in active_enrollments],
    )
    return _collect_balances_for_enrollments(
        student, active_enrollments, calculator, as_of_date, days_back, include_paid_off
    )


def _collect_balances_for_enrollments(
    student, active_enrollments, calculator, as_of_date, days_back, include_paid_off
):
    as_of = as_of_date or timezone.localdate()
    cutoff_date = as_of - datetime.timedelta(days=days_back)

    program_balances = []
    total_balance = Decimal("0.00")
    total_fees_all = Decimal("0.00")
    total_payments_all = Decimal("0.00")
    total_sliding_all = Decimal("0.00")

    for e in active_enrollments:
        try:
            b_data = calculator.get_balance_data(
                student, e.program, can_view_sliding=True
            )
        except InconsistentBalanceStatementError:
            logger.warning(
                "Monthly balance statement unavailable; financial records need review."
            )
            continue
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


def _get_student_parent_emails(
    student: Student, relationships=None
) -> List[tuple[Any, str]]:
    """Resolve opted-in local adult notification emails for a student."""
    if not student.organization_id:
        return []
    if relationships is None:
        relationships = (
            AdultStudentRelationship.objects.filter(
                organization_id=student.organization_id,
                student=student,
                adult__organization_id=student.organization_id,
            )
            .select_related("adult__user")
            .order_by("adult__last_name", "adult__legal_first_name", "pk")
        )
    local_relationships = [
        relationship
        for relationship in relationships
        if relationship.organization_id == student.organization_id
        and relationship.student_id == student.pk
        and relationship.adult.organization_id == student.organization_id
    ]
    by_id = {relationship.pk: relationship for relationship in local_relationships}
    ordered = [
        by_id[relationship_id]
        for relationship_id in (
            student.primary_contact_relationship_id,
            student.secondary_contact_relationship_id,
        )
        if relationship_id in by_id
    ] + local_relationships
    parents = []
    seen = set()
    for relationship in ordered:
        if relationship.adult_id not in seen:
            parents.append(relationship.adult)
            seen.add(relationship.adult_id)
    opted_in = [
        p for p in parents if p.email_updates and getattr(p, "login_enabled", True)
    ]

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
        Student.objects.filter(
            graduated=False,
            organization__is_active=True,
            enrollment__active=True,
            enrollment__program__active=True,
        )
        .select_related("organization")
        .distinct()
    )

    if student_id:
        active_student_qs = active_student_qs.filter(pk=student_id)

    targets = []
    include_paid_off = not positive_only
    students_by_org = defaultdict(list)
    for student in active_student_qs:
        if not student.organization_id:
            logger.warning(
                "Monthly balance student skipped due to missing organization."
            )
            continue
        students_by_org[student.organization_id].append(student)

    for students in students_by_org.values():
        organization = students[0].organization
        enrollments = _scoped_active_enrollments(organization, students)
        programs = {
            enrollment.program_id: enrollment.program
            for rows in enrollments.values()
            for enrollment in rows
        }
        calculator = TenantBalanceStatementCalculator(
            organization, students, programs.values()
        )
        relationships = defaultdict(list)
        for relationship in (
            AdultStudentRelationship.objects.for_organization(organization)
            .filter(
                student_id__in=[student.pk for student in students],
                student__organization=organization,
                adult__organization=organization,
            )
            .select_related("adult__user")
            .order_by("student_id", "adult__last_name", "adult__legal_first_name", "pk")
        ):
            relationships[relationship.student_id].append(relationship)

        for student in students:
            data = _collect_balances_for_enrollments(
                student,
                enrollments[student.pk],
                calculator,
                as_of_date,
                days_back,
                include_paid_off,
            )
            eligible_programs = [
                balance
                for balance in data["program_balances"]
                if not positive_only
                or balance["balance"] > Decimal("0.00")
                or balance["has_recent_transactions"]
            ]
            if not eligible_programs:
                continue
            parent_emails = _get_student_parent_emails(
                student, relationships[student.pk]
            )
            for balance in eligible_programs:
                statement_data = {
                    "student": student,
                    "program_balances": [balance],
                    "total_balance": balance["balance"],
                    "total_fees": balance["total_fees"],
                    "total_payments": balance["total_payments"],
                    "total_sliding": balance["total_sliding"],
                    "has_balance": bool(
                        balance["balance"] != Decimal("0.00")
                        or balance["total_fees"] > Decimal("0.00")
                    ),
                    "has_recent_activity": balance["has_recent_transactions"],
                }
                for adult, email in parent_emails:
                    targets.append(
                        {
                            "student": student,
                            "program": balance["program"],
                            "adult": adult,
                            "email": email,
                            "data": statement_data,
                        }
                    )

    return targets
