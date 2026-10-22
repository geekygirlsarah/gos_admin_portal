import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils.crypto import get_random_string

from programs.models import (
    Enrollment,
    Fee,
    FeeAssignment,
    Payment,
    Program,
    SlidingScale,
    Student,
)


class ProgramDuesOwedViewTests(TestCase):
    def setUp(self):
        password = get_random_string(32)
        self.user = User.objects.create_superuser(
            username="admin", password=password, email="admin@example.com"
        )
        self.client.login(username="admin", password=password)
        self.program = Program.objects.create(
            name="Test Robotics Program",
            start_date=datetime.date(2026, 1, 1),
            end_date=datetime.date(2026, 12, 31),
        )

        # Active Student 1: Fee 100, Payment 40 -> Dues 100, Balance 60
        self.student1 = Student.objects.create(
            legal_first_name="Alice", last_name="Active"
        )
        Enrollment.objects.create(
            student=self.student1, program=self.program, active=True
        )
        self.fee1 = Fee.objects.create(
            program=self.program,
            name="Registration",
            amount=Decimal("100.00"),
            effective_date=datetime.date(2026, 1, 15),
        )
        FeeAssignment.objects.create(fee=self.fee1, student=self.student1)
        Payment.objects.create(
            student=self.student1,
            program=self.program,
            amount=Decimal("40.00"),
            paid_on=datetime.date(2026, 2, 1),
        )

        # Active Student 2: Fee 200, Sliding scale 50% discount ($100), Payment 30 -> Dues 100, Balance 70
        self.student2 = Student.objects.create(
            legal_first_name="Bob", last_name="Active"
        )
        Enrollment.objects.create(
            student=self.student2, program=self.program, active=True
        )
        self.fee2 = Fee.objects.create(
            program=self.program,
            name="Tuition",
            amount=Decimal("200.00"),
            effective_date=datetime.date(2026, 1, 15),
        )
        FeeAssignment.objects.create(fee=self.fee2, student=self.student2)
        SlidingScale.objects.create(
            student=self.student2,
            percent=50,
            status=SlidingScale.STATUS_APPROVED,
            date=datetime.date(2026, 1, 1),
            expiration_date=datetime.date(2026, 12, 31),
        )
        Payment.objects.create(
            student=self.student2,
            program=self.program,
            amount=Decimal("30.00"),
            paid_on=datetime.date(2026, 2, 1),
        )

        # Inactive Student: Fee 150, Payment 0 -> Dues 150, Balance 150 (should be excluded from totals)
        self.student_inactive = Student.objects.create(
            legal_first_name="Charlie", last_name="Inactive"
        )
        Enrollment.objects.create(
            student=self.student_inactive, program=self.program, active=False
        )
        self.fee3 = Fee.objects.create(
            program=self.program,
            name="Fee Inactive",
            amount=Decimal("150.00"),
            effective_date=datetime.date(2026, 1, 15),
        )
        FeeAssignment.objects.create(fee=self.fee3, student=self.student_inactive)

        # Graduated Student: Fee 80, Payment 0 -> Dues 80, Balance 80 (should be excluded from totals)
        self.student_graduated = Student.objects.create(
            legal_first_name="Diana", last_name="Graduated", graduated=True
        )
        Enrollment.objects.create(
            student=self.student_graduated, program=self.program, active=True
        )
        self.fee4 = Fee.objects.create(
            program=self.program,
            name="Fee Graduated",
            amount=Decimal("80.00"),
            effective_date=datetime.date(2026, 1, 15),
        )
        FeeAssignment.objects.create(fee=self.fee4, student=self.student_graduated)

    def test_dues_and_balance_columns_and_totals(self):
        url = reverse("program_dues_owed", args=[self.program.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        active_rows = response.context["active_rows"]
        inactive_rows = response.context["inactive_rows"]

        self.assertEqual(len(active_rows), 2)
        self.assertEqual(len(inactive_rows), 2)

        # Verify active row values
        row_alice = next(r for r in active_rows if r["student"] == self.student1)
        self.assertEqual(row_alice["dues"], Decimal("100.00"))
        self.assertEqual(row_alice["balance"], Decimal("60.00"))

        row_bob = next(r for r in active_rows if r["student"] == self.student2)
        self.assertEqual(row_bob["dues"], Decimal("100.00"))
        self.assertEqual(row_bob["balance"], Decimal("70.00"))

        # Verify inactive row values
        row_charlie = next(
            r for r in inactive_rows if r["student"] == self.student_inactive
        )
        self.assertEqual(row_charlie["dues"], Decimal("150.00"))
        self.assertEqual(row_charlie["balance"], Decimal("150.00"))

        row_diana = next(
            r for r in inactive_rows if r["student"] == self.student_graduated
        )
        self.assertEqual(row_diana["dues"], Decimal("80.00"))
        self.assertEqual(row_diana["balance"], Decimal("80.00"))

        # Totals should ONLY include active students (100 + 100 = 200 dues; 60 + 70 = 130 balance)
        # Inactive students ($150 + $80 = $230) must be ignored in the totals!
        self.assertEqual(response.context["total_dues"], Decimal("200.00"))
        self.assertEqual(response.context["total_balance"], Decimal("130.00"))

        # Verify template rendering
        content = response.content.decode("utf-8")
        self.assertIn("Dues Owed", content)
        self.assertIn("Balance", content)
        self.assertIn("$200.00", content)
        self.assertIn("$130.00", content)

    def test_filter_owed_updates_totals_for_active_only(self):
        # Alice owes $60, Bob owes $70
        # If we pay Alice in full:
        Payment.objects.create(
            student=self.student1,
            program=self.program,
            amount=Decimal("60.00"),
            paid_on=datetime.date(2026, 2, 2),
        )

        url = reverse("program_dues_owed", args=[self.program.pk]) + "?filter=owed"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        active_rows = response.context["active_rows"]
        self.assertEqual(len(active_rows), 1)
        self.assertEqual(active_rows[0]["student"], self.student2)

        # Only Bob's dues and balance should be totaled
        self.assertEqual(response.context["total_dues"], Decimal("100.00"))
        self.assertEqual(response.context["total_balance"], Decimal("70.00"))
