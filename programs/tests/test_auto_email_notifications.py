from decimal import Decimal

from django.core import mail
from django.test import TestCase
from django.utils import timezone

from programs.models import (
    Adult,
    Enrollment,
    Fee,
    Payment,
    Program,
    SlidingScale,
    Student,
)


class AutoEmailNotificationsTest(TestCase):
    def setUp(self):
        self.program = Program.objects.create(name="Test Program", active=True)

        # Student and Parent
        self.student = Student.objects.create(first_name="Alice", last_name="Alpha")
        self.parent = Adult.objects.create(
            first_name="Parent",
            last_name="Alpha",
            personal_email="parent@example.com",
            email_updates=True,
            is_parent=True,
        )
        self.student.primary_contact = self.parent
        self.student.save()

        Enrollment.objects.create(student=self.student, program=self.program)

    def test_fee_added_sends_email(self):
        # Clear outbox
        mail.outbox = []

        # Add a fee to the program
        fee = Fee.objects.create(
            program=self.program, name="Registration Fee", amount=Decimal("50.00")
        )

        # Assert email was sent
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.parent.personal_email])
        self.assertIn("Registration Fee", mail.outbox[0].subject)
        self.assertIn("50.00", mail.outbox[0].body)

    def test_payment_added_sends_email(self):
        # Add a fee first so balance is interesting
        Fee.objects.create(
            program=self.program, name="Registration Fee", amount=Decimal("100.00")
        )

        # Clear outbox
        mail.outbox = []

        # Add a payment
        Payment.objects.create(
            student=self.student,
            program=self.program,
            amount=Decimal("40.00"),
            paid_on=timezone.now().date(),
            paid_via="cash",
            notes="Initial payment",
        )

        # Assert email was sent
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.parent.personal_email])
        self.assertIn("Payment Recorded", mail.outbox[0].subject)
        self.assertIn("40.00", mail.outbox[0].body)
        self.assertIn("Initial payment", mail.outbox[0].body)
        # Balance should be 100 - 40 = 60
        self.assertIn("60.00", mail.outbox[0].body)

    def test_sliding_scale_added_sends_email(self):
        # Clear outbox
        mail.outbox = []

        # Add a sliding scale
        SlidingScale.objects.create(
            student=self.student, program=self.program, percent=Decimal("50.00")
        )

        # Assert email was sent
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.parent.personal_email])
        self.assertIn("Sliding Scale Added", mail.outbox[0].subject)
        self.assertIn("50.00%", mail.outbox[0].body)

    def test_no_email_if_email_updates_false(self):
        self.parent.email_updates = False
        self.parent.save()

        mail.outbox = []
        Fee.objects.create(
            program=self.program, name="Another Fee", amount=Decimal("10.00")
        )
        self.assertEqual(len(mail.outbox), 0)
