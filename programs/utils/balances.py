"""Sliding-scale discount and student balance calculations."""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_DOWN, Decimal

from django.db.models import Q


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


def program_overlaps_sliding_window(program, sliding):
    """Return True if a program's date range overlaps the sliding scale's
    effective window (``sliding.date`` -> ``sliding.expiration_date``).

    Null dates are treated as unbounded, and the program's ``active`` flag is
    intentionally ignored: a program that isn't currently active still gets the
    discount whenever its dates overlap, so it applies automatically if the
    program becomes active again.
    """
    if program is None or sliding is None:
        return False

    prog_start = program.start_date
    prog_end = program.end_date
    slide_start = sliding.date
    slide_end = sliding.expiration_date

    if prog_start is not None and slide_end is not None and prog_start > slide_end:
        return False
    if prog_end is not None and slide_start is not None and prog_end < slide_start:
        return False
    return True


def get_active_sliding_scale(student, on_date=None):
    """Return the SlidingScale record (if any) currently in effect for a student.

    The sliding scale is no longer tied to a single program: an approved,
    non-expired record applies across ALL of the student's programs. If
    ``on_date`` is given, only records that haven't expired as of that date
    are considered (used to evaluate historical fees/balances correctly).
    """
    from ..models import SlidingScale

    qs = SlidingScale.objects.filter(
        student=student, status=SlidingScale.STATUS_APPROVED
    )
    if on_date is not None:
        qs = qs.filter(
            Q(expiration_date__isnull=True) | Q(expiration_date__gte=on_date)
        )
    else:
        qs = qs.filter(
            Q(expiration_date__isnull=True) | Q(expiration_date__gte=date.today())
        )
    return qs.order_by("-date", "-created_at").first()


def get_student_balance_data(student, program, can_view_sliding=True):
    """
    Computes entries, total fees, sliding discount, total payments, and balance for
    a student in a specific program. The sliding scale discount (if any) now
    applies across all of the student's programs, not just this one.
    """
    from ..models import Fee, Payment

    # Gather entries: fees (program), sliding scale (if exists), and payments
    entries = []
    sliding = get_active_sliding_scale(student)
    sliding_overlaps = program_overlaps_sliding_window(program, sliding)

    # Fees: positive amounts
    fees = Fee.objects.filter(program=program)
    for fee in fees:
        if (
            fee.assignments.exists()
            and not fee.assignments.filter(student=student).exists()
        ):
            continue
        fee_date = fee.effective_date or (
            fee.created_at.date() if fee.created_at else None
        )
        adjusted_amount = fee.amount
        if (
            sliding
            and sliding.percent is not None
            and can_view_sliding
            and sliding_overlaps
        ):
            starts_ok = not sliding.date or (fee_date and fee_date >= sliding.date)
            ends_ok = not sliding.expiration_date or (
                fee_date and fee_date <= sliding.expiration_date
            )
            if starts_ok and ends_ok:
                discount = compute_sliding_discount_rounded(fee.amount, sliding.percent)
                adjusted_amount = fee.amount - discount

        entries.append(
            {
                "date": fee_date,
                "due_date": fee.due_date,
                "type": "Fee",
                "name": fee.name,
                "amount": fee.amount,
                "adjusted_amount": adjusted_amount,
            }
        )

    # Compute total fees for discount: ONLY include fees applicable to this student
    # and on or after the sliding scale's effective date.
    applicable_fees_for_discount = []
    for fee in Fee.objects.filter(program=program):
        if (
            fee.assignments.exists()
            and not fee.assignments.filter(student=student).exists()
        ):
            continue

        fee_date = fee.effective_date or (
            fee.created_at.date() if fee.created_at else None
        )
        if sliding and sliding.date and fee_date and fee_date < sliding.date:
            continue
        if (
            sliding
            and sliding.expiration_date
            and fee_date
            and fee_date > sliding.expiration_date
        ):
            continue

        applicable_fees_for_discount.append(fee.amount)

    total_fees_for_discount = sum(
        applicable_fees_for_discount,
        start=Decimal("0"),
    )
    if (
        sliding
        and sliding.percent is not None
        and can_view_sliding
        and sliding_overlaps
    ):
        discount = compute_sliding_discount_rounded(
            total_fees_for_discount, sliding.percent
        )
        entries.append(
            {
                "date": sliding.date or sliding.created_at.date(),
                "type": "Sliding Scale",
                "name": f"Sliding scale (owes {sliding.percent}%)",
                "amount": Decimal("0.00"),
                "adjusted_amount": Decimal("0.00"),
            }
        )
    else:
        discount = Decimal("0")

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
                "adjusted_amount": -p.amount,
                "payment_id": p.id,
            }
        )

    # Sort by date
    entries.sort(key=lambda e: (e["date"] is None, e["date"], e["type"]))

    total_fees = sum([e["amount"] for e in entries if e["type"] == "Fee"])
    total_sliding = discount
    total_payments = -sum(
        [e["amount"] for e in entries if e["type"] == "Payment"]
    )  # positive figure
    balance = total_fees - total_sliding - total_payments

    return {
        "entries": entries,
        "total_fees": total_fees,
        "total_sliding": total_sliding,
        "total_payments": total_payments,
        "balance": balance,
        "sliding_scale": sliding,
    }


def get_student_program_balance(student, program, can_view_sliding=True):
    """Return a student's current balance for one program."""
    return get_student_balance_data(
        student,
        program,
        can_view_sliding=can_view_sliding,
    )["balance"]
