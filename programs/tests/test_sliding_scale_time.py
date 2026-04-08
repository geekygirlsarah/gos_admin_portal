import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from programs.models import (
    Enrollment,
    Fee,
    Program,
    SlidingScale,
    Student,
)


class SlidingScaleTimeRestrictedtest(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username="admin",
            password="password",
            email="admin@example.com",  # nosec B106
        )
        self.client.login(username="admin", password="password")  # nosec B106

    def test_sliding_scale_respects_date(self):
        """
        Verify that SlidingScale only applies to fees on or after its effective date.
        """
        program = Program.objects.create(name="Time Program 2")
        student = Student.objects.create(legal_first_name="Time", last_name="Student 2")
        Enrollment.objects.create(student=student, program=program)

        # Fee 1: "Past" fee (Jan 1)
        Fee.objects.create(
            program=program,
            name="Past Fee",
            amount=Decimal("100.00"),
            date=datetime.date(2026, 1, 1),
        )

        # Fee 2: "Future" fee (Mar 1)
        Fee.objects.create(
            program=program,
            name="Future Fee",
            amount=Decimal("100.00"),
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

        # It should ONLY apply to the future fee (100) * 0.5 = 50 discount
        entries = response.context["entries"]
        sliding_scale_entry = next(e for e in entries if e["type"] == "Sliding Scale")

        self.assertEqual(sliding_scale_entry["amount"], Decimal("0.00"))

        # Verify adjusted_amount on fees
        past_fee = next(e for e in entries if e["name"] == "Past Fee")
        future_fee = next(e for e in entries if e["name"] == "Future Fee")
        self.assertEqual(past_fee["adjusted_amount"], Decimal("100.00"))
        self.assertEqual(future_fee["adjusted_amount"], Decimal("50.00"))

        # total_fees (200) - discount (50) = 150 balance
        self.assertEqual(response.context["balance"], Decimal("150.00"))

    def test_sliding_scale_no_date_applies_to_all(self):
        """
        Verify that SlidingScale with no date still applies to all fees.
        """
        program = Program.objects.create(name="All Fees Program")
        student = Student.objects.create(legal_first_name="All", last_name="Fees")
        Enrollment.objects.create(student=student, program=program)

        Fee.objects.create(
            program=program,
            name="Fee 1",
            amount=Decimal("100.00"),
            date=datetime.date(2026, 1, 1),
        )
        Fee.objects.create(
            program=program,
            name="Fee 2",
            amount=Decimal("100.00"),
            date=datetime.date(2026, 3, 1),
        )

        SlidingScale.objects.create(
            student=student, program=program, percent=Decimal("50.00"), date=None
        )

        url = reverse("program_student_balance", args=[program.pk, student.pk])
        response = self.client.get(url)
        self.assertEqual(
            sliding_scale_entry := next(
                e for e in response.context["entries"] if e["type"] == "Sliding Scale"
            ),
            sliding_scale_entry,
        )
        self.assertEqual(sliding_scale_entry["amount"], Decimal("0.00"))

        # Verify adjusted_amount on fees
        fee1 = next(e for e in response.context["entries"] if e["name"] == "Fee 1")
        fee2 = next(e for e in response.context["entries"] if e["name"] == "Fee 2")
        self.assertEqual(fee1["adjusted_amount"], Decimal("50.00"))
        self.assertEqual(fee2["adjusted_amount"], Decimal("50.00"))
