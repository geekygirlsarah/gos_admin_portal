import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from programs.models import Enrollment, Fee, FeeAssignment, Payment, Program, Student


class FinanceModelTests(TestCase):
    def setUp(self):
        self.program = Program.objects.create(name="Robotics 2025")
        self.student = Student.objects.create(
            legal_first_name="Alex",
            last_name="Smith",
        )

    def test_payment_clean_requires_enrollment(self):
        pay = Payment(
            student=self.student,
            program=self.program,
            amount=Decimal("10.00"),
            paid_on=datetime.date.today(),
        )
        with self.assertRaises(ValidationError):
            pay.full_clean()
        # Enroll and then validate
        Enrollment.objects.create(student=self.student, program=self.program)
        pay.full_clean()  # no exception

    def test_fee_assignment_clean_requires_enrollment(self):
        fee = Fee.objects.create(
            program=self.program, name="Registration", amount=Decimal("50.00")
        )
        fa = FeeAssignment(fee=fee, student=self.student)
        # Not enrolled yet
        with self.assertRaises(ValidationError):
            fa.full_clean()
        # After enrollment it's valid
        Enrollment.objects.create(student=self.student, program=self.program)
        fa.full_clean()  # no exception

    def test_fee_str_representation(self):
        fee = Fee.objects.create(
            program=self.program, name="Tool Fee", amount=Decimal("75.00")
        )
        self.assertIn("Tool Fee", str(fee))
        self.assertIn("75.00", str(fee))

    def test_payment_str_representation(self):
        Enrollment.objects.create(student=self.student, program=self.program)
        pay = Payment.objects.create(
            student=self.student,
            program=self.program,
            amount=Decimal("25.00"),
            paid_on=datetime.date(2025, 3, 1),
            paid_via="check",
            check_number="1234",
        )
        self.assertIn("Alex Smith", str(pay))
        self.assertIn("25.00", str(pay))
