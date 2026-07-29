from datetime import date
from decimal import Decimal

from django.core.management import call_command
from django.db import models
from django.test import TestCase

from programs.models import Adult, Enrollment, Fee, Payment, Program, School, SlidingScale, Student


class SeedDbCommandTest(TestCase):
    def test_seed_db_command(self):
        """The seed command should create a large, varied modern dataset."""
        call_command("seed_db")

        today = date.today()

        # Check high-volume core records
        self.assertGreaterEqual(Program.objects.count(), 12)
        self.assertGreaterEqual(School.objects.count(), 4)
        self.assertGreaterEqual(Adult.objects.count(), 18)
        self.assertGreaterEqual(Student.objects.count(), 12)
        self.assertGreaterEqual(Enrollment.objects.count(), 30)

        # Check program date variety: past, current, and future offerings
        self.assertGreaterEqual(Program.objects.filter(end_date__lt=today).count(), 4)
        self.assertGreaterEqual(
            Program.objects.filter(start_date__lte=today, end_date__gte=today).count(),
            4,
        )
        self.assertGreaterEqual(Program.objects.filter(start_date__gt=today).count(), 4)

        # Check role variety among adults
        self.assertGreaterEqual(Adult.objects.filter(is_parent=True).count(), 12)
        self.assertGreaterEqual(Adult.objects.filter(is_mentor=True).count(), 4)
        self.assertGreaterEqual(Adult.objects.filter(is_alumni=True).count(), 2)

        # Check financial model coverage
        self.assertGreaterEqual(Fee.objects.count(), 20)
        self.assertGreaterEqual(Payment.objects.count(), 20)
        self.assertGreaterEqual(SlidingScale.objects.count(), 6)

        # Ensure payment states include paid-off, partially paid, and unpaid
        balance_profiles = {"paid": 0, "partial": 0, "unpaid": 0}
        for enrollment in Enrollment.objects.select_related("student", "program"):
            total_fees = (
                Fee.objects.filter(program=enrollment.program).aggregate(total=models.Sum("amount"))["total"]
                or Decimal("0")
            )
            total_payments = (
                Payment.objects.filter(
                    student=enrollment.student,
                    program=enrollment.program,
                ).aggregate(total=models.Sum("amount"))["total"]
                or Decimal("0")
            )

            if total_fees == Decimal("0"):
                continue
            if total_payments == Decimal("0"):
                balance_profiles["unpaid"] += 1
            elif total_payments < total_fees:
                balance_profiles["partial"] += 1
            elif total_payments >= total_fees:
                balance_profiles["paid"] += 1

        self.assertGreater(balance_profiles["paid"], 0)
        self.assertGreater(balance_profiles["partial"], 0)
        self.assertGreater(balance_profiles["unpaid"], 0)

    def test_seed_db_command_is_idempotent(self):
        """Running the command twice should not duplicate seeded entities."""
        call_command("seed_db")
        first_counts = {
            "programs": Program.objects.count(),
            "schools": School.objects.count(),
            "adults": Adult.objects.count(),
            "students": Student.objects.count(),
            "enrollments": Enrollment.objects.count(),
            "fees": Fee.objects.count(),
            "payments": Payment.objects.count(),
            "sliding_scales": SlidingScale.objects.count(),
        }

        call_command("seed_db")
        second_counts = {
            "programs": Program.objects.count(),
            "schools": School.objects.count(),
            "adults": Adult.objects.count(),
            "students": Student.objects.count(),
            "enrollments": Enrollment.objects.count(),
            "fees": Fee.objects.count(),
            "payments": Payment.objects.count(),
            "sliding_scales": SlidingScale.objects.count(),
        }

        self.assertEqual(first_counts, second_counts)
