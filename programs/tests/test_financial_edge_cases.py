"""Comprehensive test suite for financial balance calculations, fee assignments,
sliding scale date windows, multi-program isolation, and financial edge cases.
"""

import datetime
from decimal import Decimal

from django.test import TestCase

from programs.models import (
    Enrollment,
    Fee,
    FeeAssignment,
    Payment,
    Program,
    SlidingScale,
    Student,
)
from programs.utils.balances import (
    compute_sliding_discount_rounded,
    get_student_balance_data,
    get_student_program_balance,
    program_overlaps_sliding_window,
)


class SlidingScaleRoundingEdgeCasesTests(TestCase):
    """Test compute_sliding_discount_rounded precision and boundary conditions."""

    def test_half_down_rounding_at_exact_half_dollar_boundaries(self):
        # 50% of $25.00 = $12.50 -> rounded to $12 (half-down)
        self.assertEqual(
            compute_sliding_discount_rounded(Decimal("25.00"), Decimal("50")),
            Decimal("12"),
        )
        # 50% of $27.00 = $13.50 -> rounded to $13 (half-down)
        self.assertEqual(
            compute_sliding_discount_rounded(Decimal("27.00"), Decimal("50")),
            Decimal("13"),
        )
        # 50% of $29.00 = $14.50 -> rounded to $14 (half-down)
        self.assertEqual(
            compute_sliding_discount_rounded(Decimal("29.00"), Decimal("50")),
            Decimal("14"),
        )
        # 50% of $1.00 = $0.50 -> rounded to $0 (half-down)
        self.assertEqual(
            compute_sliding_discount_rounded(Decimal("1.00"), Decimal("50")),
            Decimal("0"),
        )

    def test_standard_fractional_rounding_above_and_below_half(self):
        # 25% of $49.00 = $12.25 -> rounds down to $12
        self.assertEqual(
            compute_sliding_discount_rounded(Decimal("49.00"), Decimal("25")),
            Decimal("12"),
        )
        # 75% of $17.00 = $12.75 -> rounds up to $13
        self.assertEqual(
            compute_sliding_discount_rounded(Decimal("17.00"), Decimal("75")),
            Decimal("13"),
        )
        # 33.33% of $100.00 = $33.33 -> rounds to $33
        self.assertEqual(
            compute_sliding_discount_rounded(Decimal("100.00"), Decimal("33.33")),
            Decimal("33"),
        )

    def test_extreme_and_zero_percentages(self):
        # 0% discount
        self.assertEqual(
            compute_sliding_discount_rounded(Decimal("250.00"), Decimal("0")),
            Decimal("0"),
        )
        # 100% discount
        self.assertEqual(
            compute_sliding_discount_rounded(Decimal("250.00"), Decimal("100")),
            Decimal("250"),
        )
        # 0 total fees
        self.assertEqual(
            compute_sliding_discount_rounded(Decimal("0.00"), Decimal("50")),
            Decimal("0"),
        )

    def test_none_and_invalid_inputs(self):
        self.assertEqual(
            compute_sliding_discount_rounded(None, Decimal("50")),
            Decimal("0"),
        )
        self.assertEqual(
            compute_sliding_discount_rounded(Decimal("100"), None),
            Decimal("0"),
        )
        self.assertEqual(
            compute_sliding_discount_rounded(None, None),
            Decimal("0"),
        )


class FinancialApplicabilityEdgeCasesTests(TestCase):
    """Test fee assignment filtering and sliding scale interaction with assigned fees."""

    def setUp(self):
        self.program = Program.objects.create(name="FRC 2026", active=True)
        self.student1 = Student.objects.create(legal_first_name="Alice", last_name="A")
        self.student2 = Student.objects.create(legal_first_name="Bob", last_name="B")
        self.student3 = Student.objects.create(
            legal_first_name="Charlie", last_name="C"
        )

        Enrollment.objects.create(
            student=self.student1, program=self.program, active=True
        )
        Enrollment.objects.create(
            student=self.student2, program=self.program, active=True
        )
        Enrollment.objects.create(
            student=self.student3, program=self.program, active=True
        )

        # Fee 1: General (no assignments -> all 3 students)
        self.fee_general = Fee.objects.create(
            program=self.program, name="General Fee", amount=Decimal("100.00")
        )
        # Fee 2: Specific to Student 1
        self.fee_s1 = Fee.objects.create(
            program=self.program, name="Kit Fee S1", amount=Decimal("50.00")
        )
        FeeAssignment.objects.create(fee=self.fee_s1, student=self.student1)

        # Fee 3: Specific to Student 2
        self.fee_s2 = Fee.objects.create(
            program=self.program, name="Tool Fee S2", amount=Decimal("200.00")
        )
        FeeAssignment.objects.create(fee=self.fee_s2, student=self.student2)

    def test_fee_totals_match_applicability(self):
        # Student 1: General ($100) + Kit ($50) = $150
        data1 = get_student_balance_data(self.student1, self.program)
        self.assertEqual(data1["total_fees"], Decimal("150.00"))
        self.assertEqual(len([e for e in data1["entries"] if e["type"] == "Fee"]), 2)

        # Student 2: General ($100) + Tool ($200) = $300
        data2 = get_student_balance_data(self.student2, self.program)
        self.assertEqual(data2["total_fees"], Decimal("300.00"))
        self.assertEqual(len([e for e in data2["entries"] if e["type"] == "Fee"]), 2)

        # Student 3: General ($100) only = $100
        data3 = get_student_balance_data(self.student3, self.program)
        self.assertEqual(data3["total_fees"], Decimal("100.00"))
        self.assertEqual(len([e for e in data3["entries"] if e["type"] == "Fee"]), 1)

    def test_sliding_scale_applied_only_to_applicable_fees(self):
        # 50% discount on Student 1 (total applicable $150 -> discount $75 -> balance $75)
        SlidingScale.objects.create(
            student=self.student1,
            percent=50,
            status=SlidingScale.STATUS_APPROVED,
        )
        data1 = get_student_balance_data(self.student1, self.program)
        self.assertEqual(data1["total_fees"], Decimal("150.00"))
        self.assertEqual(data1["total_sliding"], Decimal("75.00"))
        self.assertEqual(data1["balance"], Decimal("75.00"))

        # 50% discount on Student 2 (total applicable $300 -> discount $150 -> balance $150)
        SlidingScale.objects.create(
            student=self.student2,
            percent=50,
            status=SlidingScale.STATUS_APPROVED,
        )
        data2 = get_student_balance_data(self.student2, self.program)
        self.assertEqual(data2["total_fees"], Decimal("300.00"))
        self.assertEqual(data2["total_sliding"], Decimal("150.00"))
        self.assertEqual(data2["balance"], Decimal("150.00"))


class SlidingScaleDateWindowEdgeCasesTests(TestCase):
    """Test effective date and expiration date window rules for sliding scales."""

    def setUp(self):
        self.program = Program.objects.create(
            name="2026 Program",
            start_date=datetime.date(2026, 1, 1),
            end_date=datetime.date(2026, 12, 31),
            active=True,
        )
        self.student = Student.objects.create(legal_first_name="David", last_name="D")
        Enrollment.objects.create(
            student=self.student, program=self.program, active=True
        )

        # 3 Fees with different effective dates
        self.fee_early = Fee.objects.create(
            program=self.program,
            name="Early Registration",
            amount=Decimal("100.00"),
            effective_date=datetime.date(2026, 1, 15),
        )
        self.fee_mid = Fee.objects.create(
            program=self.program,
            name="Midseason Fee",
            amount=Decimal("100.00"),
            effective_date=datetime.date(2026, 4, 15),
        )
        self.fee_late = Fee.objects.create(
            program=self.program,
            name="Late Season Fee",
            amount=Decimal("100.00"),
            effective_date=datetime.date(2026, 8, 15),
        )

    def test_sliding_scale_effective_window_boundary_inclusion(self):
        # Sliding scale valid only from 2026-03-01 to 2026-06-30 (50%)
        # Only fee_mid (2026-04-15) falls in window -> $50 discount
        SlidingScale.objects.create(
            student=self.student,
            percent=50,
            date=datetime.date(2026, 3, 1),
            expiration_date=datetime.date(2026, 6, 30),
            status=SlidingScale.STATUS_APPROVED,
        )
        data = get_student_balance_data(self.student, self.program)
        self.assertEqual(data["total_fees"], Decimal("300.00"))
        self.assertEqual(data["total_sliding"], Decimal("50.00"))
        self.assertEqual(data["balance"], Decimal("250.00"))

    def test_open_ended_sliding_scale_windows(self):
        # Open start date (date=None, expiration_date=2026-05-01)
        # Covers fee_early (Jan) and fee_mid (Apr), but not fee_late (Aug)
        s = SlidingScale.objects.create(
            student=self.student,
            percent=50,
            date=None,
            expiration_date=datetime.date(2026, 5, 1),
            status=SlidingScale.STATUS_APPROVED,
        )
        data = get_student_balance_data(self.student, self.program)
        self.assertEqual(data["total_fees"], Decimal("300.00"))
        self.assertEqual(
            data["total_sliding"], Decimal("100.00")
        )  # $100 + $100 -> $100 discount
        self.assertEqual(data["balance"], Decimal("200.00"))

        # Update to open end date (date=2026-04-01, expiration_date=None)
        # Covers fee_mid (Apr) and fee_late (Aug), but not fee_early (Jan)
        s.date = datetime.date(2026, 4, 1)
        s.expiration_date = None
        s.save()
        data2 = get_student_balance_data(self.student, self.program)
        self.assertEqual(data2["total_sliding"], Decimal("100.00"))
        self.assertEqual(data2["balance"], Decimal("200.00"))

    def test_non_approved_sliding_scales_do_not_apply(self):
        # Pending sliding scale
        SlidingScale.objects.create(
            student=self.student,
            percent=50,
            status=SlidingScale.STATUS_PENDING,
        )
        data = get_student_balance_data(self.student, self.program)
        self.assertEqual(data["total_sliding"], Decimal("0"))
        self.assertEqual(data["balance"], Decimal("300.00"))


class MultiProgramAndOverpaymentEdgeCasesTests(TestCase):
    """Test cross-program isolation, overpayments, credit balances, and payment types."""

    def setUp(self):
        self.p1 = Program.objects.create(name="Program A", active=True)
        self.p2 = Program.objects.create(name="Program B", active=True)

        self.student = Student.objects.create(legal_first_name="Elena", last_name="E")
        Enrollment.objects.create(student=self.student, program=self.p1, active=True)
        Enrollment.objects.create(student=self.student, program=self.p2, active=True)

        self.fee1 = Fee.objects.create(
            program=self.p1, name="Fee P1", amount=Decimal("200.00")
        )
        self.fee2 = Fee.objects.create(
            program=self.p2, name="Fee P2", amount=Decimal("300.00")
        )

        # Approved student-wide sliding scale (50%)
        SlidingScale.objects.create(
            student=self.student,
            percent=50,
            status=SlidingScale.STATUS_APPROVED,
        )

    def test_cross_program_discount_and_payment_isolation(self):
        # Both programs get 50% discount
        self.assertEqual(
            get_student_program_balance(self.student, self.p1), Decimal("100.00")
        )
        self.assertEqual(
            get_student_program_balance(self.student, self.p2), Decimal("150.00")
        )

        # Pay $100 on Program 1
        Payment.objects.create(
            student=self.student,
            program=self.p1,
            amount=Decimal("100.00"),
            paid_on=datetime.date.today(),
            paid_via="check",
            check_number="9988",
        )

        # Program 1 is paid off ($0.00), Program 2 is still $150.00
        self.assertEqual(
            get_student_program_balance(self.student, self.p1), Decimal("0.00")
        )
        self.assertEqual(
            get_student_program_balance(self.student, self.p2), Decimal("150.00")
        )

    def test_overpayment_and_account_credit(self):
        # Pay $150 on Program 1 ($100 owed after 50% discount) -> balance should be -$50.00
        Payment.objects.create(
            student=self.student,
            program=self.p1,
            amount=Decimal("150.00"),
            paid_on=datetime.date.today(),
            paid_via="credit_card",
        )
        data = get_student_balance_data(self.student, self.p1)
        self.assertEqual(data["balance"], Decimal("-50.00"))
        self.assertEqual(data["total_payments"], Decimal("150.00"))

    def test_can_view_sliding_flag_hides_discount(self):
        # When can_view_sliding is False, full fee amount is reflected without discount
        data = get_student_balance_data(self.student, self.p1, can_view_sliding=False)
        self.assertEqual(data["total_sliding"], Decimal("0"))
        self.assertEqual(data["balance"], Decimal("200.00"))


class ProgramOverlapUtilityTests(TestCase):
    """Test program_overlaps_sliding_window edge cases."""

    def test_null_inputs(self):
        self.assertFalse(program_overlaps_sliding_window(None, None))
        p = Program(name="Test")
        self.assertFalse(program_overlaps_sliding_window(p, None))
        s = SlidingScale(percent=50)
        self.assertFalse(program_overlaps_sliding_window(None, s))

    def test_unbounded_and_fee_inferred_dates(self):
        student = Student.objects.create(legal_first_name="Test", last_name="Student")
        p = Program.objects.create(name="Undated Program")
        Enrollment.objects.create(student=student, program=p)
        Fee.objects.create(
            program=p,
            name="Dated Fee",
            amount=Decimal("50.00"),
            effective_date=datetime.date(2026, 5, 10),
        )

        s = SlidingScale.objects.create(
            student=student,
            percent=50,
            date=datetime.date(2026, 1, 1),
            expiration_date=datetime.date(2026, 12, 31),
            status=SlidingScale.STATUS_APPROVED,
        )
        # Overlaps based on fee dates
        self.assertTrue(program_overlaps_sliding_window(p, s))

        # Does not overlap if sliding scale is outside fee dates
        s2 = SlidingScale.objects.create(
            student=student,
            percent=50,
            date=datetime.date(2025, 1, 1),
            expiration_date=datetime.date(2025, 12, 31),
            status=SlidingScale.STATUS_APPROVED,
        )
        self.assertFalse(program_overlaps_sliding_window(p, s2))
