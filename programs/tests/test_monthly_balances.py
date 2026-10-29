"""Tests for the monthly balances update service and management command."""

import datetime
from decimal import Decimal
from io import StringIO

from django.core import mail
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from programs.models import (
    Adult,
    AdultStudentRelationship,
    Enrollment,
    Fee,
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

    def test_collect_monthly_balances_excludes_old_paid_off_programs(self):
        # Program 2 paid off 60 days ago
        old_program = Program.objects.create(
            name="Robotics Season 2024",
            active=True,
            start_date=self.today - datetime.timedelta(days=700),
            end_date=self.today - datetime.timedelta(days=600),
        )
        Enrollment.objects.create(
            student=self.student, program=old_program, active=True
        )
        Fee.objects.create(
            program=old_program,
            name="Old Registration Fee",
            amount=Decimal("150.00"),
            effective_date=self.today - datetime.timedelta(days=650),
        )
        Payment.objects.create(
            student=self.student,
            program=old_program,
            amount=Decimal("150.00"),
            paid_via="check",
            paid_on=self.today - datetime.timedelta(days=600),
        )

        # Current program has balance $200 (no payments yet)
        data = collect_monthly_balances_data(self.student)
        self.assertEqual(len(data["program_balances"]), 1)
        self.assertEqual(data["program_balances"][0]["program"], self.program)
        self.assertEqual(data["total_balance"], Decimal("200.00"))
        self.assertEqual(data["total_fees"], Decimal("200.00"))

    def test_collect_monthly_balances_includes_zero_balance_with_recent_payment(self):
        # Full payment made 5 days ago
        Payment.objects.create(
            student=self.student,
            program=self.program,
            amount=Decimal("200.00"),
            paid_via="online",
            paid_on=self.today - datetime.timedelta(days=5),
        )

        data = collect_monthly_balances_data(self.student)
        self.assertEqual(data["total_balance"], Decimal("0.00"))
        self.assertTrue(data["has_recent_activity"])
        self.assertEqual(len(data["program_balances"]), 1)
        self.assertEqual(data["program_balances"][0]["program"], self.program)
        self.assertEqual(data["program_balances"][0]["balance"], Decimal("0.00"))

    def test_collect_monthly_balances_excludes_zero_balance_with_old_payment(self):
        # Set fee to old date as well
        self.fee.effective_date = self.today - datetime.timedelta(days=60)
        self.fee.save()

        # Full payment made 60 days ago
        Payment.objects.create(
            student=self.student,
            program=self.program,
            amount=Decimal("200.00"),
            paid_via="online",
            paid_on=self.today - datetime.timedelta(days=60),
        )

        data = collect_monthly_balances_data(self.student)
        self.assertEqual(data["total_balance"], Decimal("0.00"))
        self.assertFalse(data.get("has_recent_activity", False))
        self.assertEqual(len(data["program_balances"]), 0)

    def test_collect_monthly_balances_include_paid_off_flag(self):
        self.fee.effective_date = self.today - datetime.timedelta(days=60)
        self.fee.save()

        Payment.objects.create(
            student=self.student,
            program=self.program,
            amount=Decimal("200.00"),
            paid_via="online",
            paid_on=self.today - datetime.timedelta(days=60),
        )

        data = collect_monthly_balances_data(self.student, include_paid_off=True)
        self.assertEqual(len(data["program_balances"]), 1)
        self.assertEqual(data["total_balance"], Decimal("0.00"))

    def test_recipients_includes_zero_balance_with_recent_payment(self):
        self.fee.effective_date = self.today - datetime.timedelta(days=60)
        self.fee.save()

        Payment.objects.create(
            student=self.student,
            program=self.program,
            amount=Decimal("200.00"),
            paid_via="online",
            paid_on=self.today - datetime.timedelta(days=3),
        )

        recipients = get_monthly_balance_recipients(positive_only=True)
        emails = [r["email"] for r in recipients]
        self.assertIn("patricia@example.com", emails)

    def test_recipients_excludes_zero_balance_with_old_payment(self):
        self.fee.effective_date = self.today - datetime.timedelta(days=60)
        self.fee.save()

        Payment.objects.create(
            student=self.student,
            program=self.program,
            amount=Decimal("200.00"),
            paid_via="online",
            paid_on=self.today - datetime.timedelta(days=60),
        )

        recipients = get_monthly_balance_recipients(positive_only=True)
        emails = [r["email"] for r in recipients]
        self.assertNotIn("patricia@example.com", emails)


class SendMonthlyBalancesCommandTests(TestCase):
    def setUp(self):
        self.out = StringIO()
        self.today = timezone.localdate()
        self.program = Program.objects.create(
            name="Robotics Season 2026",
            active=True,
        )

        # Student 1: owes money ($150)
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

        # Student 2: paid in full recently (3 days ago) -> owes $0 but made recent payment
        self.student2 = Student.objects.create(
            legal_first_name="Student",
            last_name="PaidRecently",
            personal_email="paid_recent@example.com",
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

        Payment.objects.create(
            student=self.student2,
            program=self.program,
            amount=Decimal("150.00"),
            paid_via="online",
            paid_on=self.today - datetime.timedelta(days=3),
        )

        # Student 3: paid in full long ago (60 days ago) in an older program -> owes $0, no recent activity
        self.old_program = Program.objects.create(
            name="Robotics Season 2025",
            active=True,
        )
        self.student3 = Student.objects.create(
            legal_first_name="Student",
            last_name="PaidLongAgo",
            personal_email="paid_old@example.com",
        )
        Enrollment.objects.create(
            student=self.student3, program=self.old_program, active=True
        )
        self.parent3 = Adult.objects.create(
            legal_first_name="Parent",
            last_name="Three",
            is_parent=True,
            personal_email="parent3@example.com",
            email_updates=True,
        )
        self.student3.primary_contact = self.parent3
        self.student3.save()

        Fee.objects.create(
            program=self.old_program,
            name="Old Season Fee",
            amount=Decimal("150.00"),
            effective_date=self.today - datetime.timedelta(days=60),
        )

        Payment.objects.create(
            student=self.student3,
            program=self.old_program,
            amount=Decimal("150.00"),
            paid_via="online",
            paid_on=self.today - datetime.timedelta(days=60),
        )

    def test_command_dry_run(self):
        mail.outbox.clear()
        call_command("send_monthly_balances", dry_run=True, stdout=self.out)
        self.assertEqual(len(mail.outbox), 0)
        output = self.out.getvalue()
        self.assertIn("Dry run complete", output)

    def test_command_sends_to_positive_balances_and_recent_transactions_by_default(
        self,
    ):
        mail.outbox.clear()
        call_command("send_monthly_balances", stdout=self.out)
        self.assertEqual(len(mail.outbox), 2)
        recipients = [m.to[0] for m in mail.outbox]
        self.assertIn("parent1@example.com", recipients)
        self.assertIn("parent2@example.com", recipients)
        self.assertNotIn("parent3@example.com", recipients)

    def test_command_all_balances_includes_old_paid_off(self):
        mail.outbox.clear()
        call_command("send_monthly_balances", all_balances=True, stdout=self.out)
        self.assertEqual(len(mail.outbox), 3)
        recipients = [m.to[0] for m in mail.outbox]
        self.assertIn("parent1@example.com", recipients)
        self.assertIn("parent2@example.com", recipients)
        self.assertIn("parent3@example.com", recipients)

    def test_command_excludes_old_paid_off_programs_from_email_context(self):
        # Enroll student 1 in an old program paid off 2 years ago
        old_program = Program.objects.create(
            name="Robotics Season 2024",
            active=True,
        )
        Enrollment.objects.create(
            student=self.student1, program=old_program, active=True
        )
        Fee.objects.create(
            program=old_program,
            name="Old Fee",
            amount=Decimal("300.00"),
            effective_date=self.today - datetime.timedelta(days=700),
        )
        Payment.objects.create(
            student=self.student1,
            program=old_program,
            amount=Decimal("300.00"),
            paid_via="online",
            paid_on=self.today - datetime.timedelta(days=650),
        )

        mail.outbox.clear()
        call_command(
            "send_monthly_balances", student_id=self.student1.pk, stdout=self.out
        )
        self.assertEqual(len(mail.outbox), 1)
        email_body = mail.outbox[0].body
        self.assertIn("Robotics Season 2026", email_body)
        self.assertNotIn("Robotics Season 2024", email_body)

    def test_command_filter_by_student(self):
        mail.outbox.clear()
        call_command(
            "send_monthly_balances",
            student_id=self.student1.pk,
            stdout=self.out,
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["parent1@example.com"])
        month_year = self.today.strftime("%B %Y")
        self.assertEqual(
            mail.outbox[0].subject,
            f"Monthly Balance Statement for {self.student1.display_name} - {month_year}",
        )

    def test_command_custom_date_and_days_back(self):
        # If running as-of 70 days in the future, student 2's payment from 3 days ago is now old
        target_date = self.today + datetime.timedelta(days=70)
        future_date = target_date.isoformat()
        mail.outbox.clear()
        call_command(
            "send_monthly_balances",
            date=future_date,
            days_back=10,
            stdout=self.out,
        )
        # student1 has positive balance so receives it; student2 and student3 paid long before future_date - 10 days
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["parent1@example.com"])
        month_year = target_date.strftime("%B %Y")
        self.assertEqual(
            mail.outbox[0].subject,
            f"Monthly Balance Statement for {self.student1.display_name} - {month_year}",
        )

    def test_command_invalid_date_format(self):
        out = StringIO()
        err = StringIO()
        call_command(
            "send_monthly_balances",
            date="invalid-date",
            stdout=out,
            stderr=err,
        )
        self.assertIn("Invalid date format", err.getvalue())
