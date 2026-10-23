"""Tests for the monthly balances update service and management command."""

import datetime
from decimal import Decimal
from io import StringIO

from django.contrib.auth.models import User
from django.core import mail
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from programs.models import (
    Adult,
    AdultStudentRelationship,
    Enrollment,
    Fee,
    FeeAssignment,
    Payment,
    Program,
    SlidingScale,
    Student,
)
from programs.services.balances_digest import (
    collect_monthly_balances_data,
    get_monthly_balance_recipients,
)


class MonthlyBalancesServiceTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.program = Program.objects.create(
            name="Robotics Season 2026",
            active=True,
            start_date=self.today - datetime.timedelta(days=30),
            end_date=self.today + datetime.timedelta(days=90),
        )

        self.student = Student.objects.create(
            legal_first_name="Alice",
            preferred_first_name="Alice",
            last_name="Johnson",
            personal_email="alice@example.com",
        )
        Enrollment.objects.create(
            student=self.student, program=self.program, active=True
        )

        self.parent = Adult.objects.create(
            legal_first_name="Patricia",
            last_name="Johnson",
            is_parent=True,
            personal_email="patricia@example.com",
            email_updates=True,
        )
        AdultStudentRelationship.objects.create(
            adult=self.parent, student=self.student, relationship_to_student="parent"
        )
        self.student.primary_contact = self.parent
        self.student.save()

        # Fee of $200
        self.fee = Fee.objects.create(
            program=self.program,
            name="Program Registration Fee",
            amount=Decimal("200.00"),
            due_date=self.today + datetime.timedelta(days=14),
        )

    def test_collect_monthly_balances_data_with_balance(self):
        # Payment of $50
        Payment.objects.create(
            student=self.student,
            program=self.program,
            amount=Decimal("50.00"),
            paid_via="online",
            paid_on=self.today - datetime.timedelta(days=5),
        )

        data = collect_monthly_balances_data(self.student)
        self.assertTrue(data["has_balance"])
        self.assertEqual(data["total_balance"], Decimal("150.00"))
        self.assertEqual(len(data["program_balances"]), 1)

        prog_data = data["program_balances"][0]
        self.assertEqual(prog_data["program"], self.program)
        self.assertEqual(prog_data["total_fees"], Decimal("200.00"))
        self.assertEqual(prog_data["total_payments"], Decimal("50.00"))
        self.assertEqual(prog_data["balance"], Decimal("150.00"))

    def test_collect_monthly_balances_with_sliding_scale(self):
        SlidingScale.objects.create(
            student=self.student,
            status="approved",
            percent=50,
            date=self.today - datetime.timedelta(days=40),
        )

        data = collect_monthly_balances_data(self.student)
        self.assertEqual(data["total_balance"], Decimal("100.00"))
        prog_data = data["program_balances"][0]
        self.assertEqual(prog_data["total_sliding"], Decimal("100.00"))
        self.assertEqual(prog_data["balance"], Decimal("100.00"))

    def test_recipients_resolution_and_fallback(self):
        # Unset email_updates on parent
        self.parent.email_updates = False
        self.parent.save()

        # Should still resolve the primary contact so parents are never omitted from billing notices
        recipients = get_monthly_balance_recipients(positive_only=True)
        emails = [r["email"] for r in recipients]
        self.assertIn("patricia@example.com", emails)


class SendMonthlyBalancesCommandTests(TestCase):
    def setUp(self):
        self.out = StringIO()
        self.today = timezone.localdate()
        self.program = Program.objects.create(
            name="Robotics Season 2026",
            active=True,
        )

        self.student1 = Student.objects.create(
            legal_first_name="Student",
            last_name="OwesMoney",
            personal_email="owes@example.com",
        )
        Enrollment.objects.create(
            student=self.student1, program=self.program, active=True
        )
        self.parent1 = Adult.objects.create(
            legal_first_name="Parent",
            last_name="One",
            is_parent=True,
            personal_email="parent1@example.com",
            email_updates=True,
        )
        self.student1.primary_contact = self.parent1
        self.student1.save()

        Fee.objects.create(
            program=self.program,
            name="Season Fee",
            amount=Decimal("150.00"),
        )

        self.student2 = Student.objects.create(
            legal_first_name="Student",
            last_name="PaidInFull",
            personal_email="paid@example.com",
        )
        Enrollment.objects.create(
            student=self.student2, program=self.program, active=True
        )
        self.parent2 = Adult.objects.create(
            legal_first_name="Parent",
            last_name="Two",
            is_parent=True,
            personal_email="parent2@example.com",
            email_updates=True,
        )
        self.student2.primary_contact = self.parent2
        self.student2.save()

        # Student 2 paid the fee
        Payment.objects.create(
            student=self.student2,
            program=self.program,
            amount=Decimal("150.00"),
            paid_via="online",
            paid_on=self.today,
        )

    def test_command_dry_run(self):
        mail.outbox.clear()
        call_command("send_monthly_balances", dry_run=True, stdout=self.out)
        self.assertEqual(len(mail.outbox), 0)
        output = self.out.getvalue()
        self.assertIn("Dry run complete", output)

    def test_command_sends_to_positive_balances_only_by_default(self):
        mail.outbox.clear()
        call_command("send_monthly_balances", stdout=self.out)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["parent1@example.com"])

    def test_command_all_balances_includes_zero_balance(self):
        mail.outbox.clear()
        call_command("send_monthly_balances", all_balances=True, stdout=self.out)
        self.assertEqual(len(mail.outbox), 2)
        recipients = [m.to[0] for m in mail.outbox]
        self.assertIn("parent1@example.com", recipients)
        self.assertIn("parent2@example.com", recipients)

    def test_command_filter_by_student(self):
        mail.outbox.clear()
        call_command(
            "send_monthly_balances",
            student_id=self.student1.pk,
            stdout=self.out,
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["parent1@example.com"])
