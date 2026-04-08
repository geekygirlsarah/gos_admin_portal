import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from programs.models import (
    Enrollment,
    Fee,
    Program,
    SlidingScale,
    Student,
)


class BalanceSheetSlidingScaleTest(TestCase):
    def setUp(self):
        password = "password"  # nosec B105
        self.user = User.objects.create_superuser(
            username="admin",
            password=password,  # nosec B105
            email="admin@example.com",
        )
        self.client.login(username="admin", password=password)  # nosec B105

    def test_adjusted_rate_column_presence_and_values(self):
        """
        Verify that the balance sheet entries have an adjusted_amount
        when a sliding scale is present.
        """
        program = Program.objects.create(name="Sliding Program")
        student = Student.objects.create(
            legal_first_name="Sliding", last_name="Student"
        )
        Enrollment.objects.create(student=student, program=program)

        # Fee 1: Before sliding scale (Jan 1)
        Fee.objects.create(
            program=program,
            name="Early Fee",
            amount=Decimal("100.00"),
            date=datetime.date(2026, 1, 1),
        )

        # Fee 2: After sliding scale (Mar 1)
        Fee.objects.create(
            program=program,
            name="Late Fee",
            amount=Decimal("200.00"),
            date=datetime.date(2026, 3, 1),
        )

        # Sliding scale: 50% discount starting Feb 1
        SlidingScale.objects.create(
            student=student,
            program=program,
            percent=Decimal("50.00"),
            date=datetime.date(2026, 2, 1),
        )

        url = reverse("program_student_balance", args=[program.pk, student.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        entries = response.context["entries"]

        # Early Fee should NOT be adjusted
        early_fee = next(e for e in entries if e["name"] == "Early Fee")
        self.assertEqual(early_fee["adjusted_amount"], Decimal("100.00"))

        # Late Fee SHOULD be adjusted (200 - 50% = 100)
        late_fee = next(e for e in entries if e["name"] == "Late Fee")
        self.assertEqual(late_fee["adjusted_amount"], Decimal("100.00"))

        # Sliding Scale entry amount should be 0
        sliding_entry = next(e for e in entries if e["type"] == "Sliding Scale")
        self.assertEqual(sliding_entry["amount"], Decimal("0.00"))

        # Total sliding (discount) should be 100 (50% of 200)
        self.assertEqual(response.context["total_sliding"], Decimal("100.00"))
        # Balance: 100 (Early) + 200 (Late) - 100 (Discount) = 200
        self.assertEqual(response.context["balance"], Decimal("200.00"))
