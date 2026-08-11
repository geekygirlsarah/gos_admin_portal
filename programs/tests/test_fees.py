"""Fee-related tests: due dates, effective dates, and form behaviors."""

from datetime import date
from decimal import Decimal

from django.core import mail
from django.test import TestCase, override_settings

from programs.forms import PaymentForm, ProgramEmailForm, SlidingScaleForm
from programs.models import (
    Enrollment,
    Fee,
    Program,
    SlidingScale,
    Student,
)
from programs.views import compute_sliding_discount_rounded


class FeeDueDateTest(TestCase):
    def setUp(self):
        self.program = Program.objects.create(name="Test Program")
        self.student = Student.objects.create(first_name="John", last_name="Doe")
        Enrollment.objects.create(student=self.student, program=self.program)

    def test_fee_due_date_field(self):
        due_date = date(2026, 8, 1)
        fee = Fee.objects.create(
            program=self.program,
            name="Registration Fee",
            amount=100.00,
            due_date=due_date,
        )
        self.assertEqual(fee.due_date, due_date)

    def test_fee_due_date_in_balance_data(self):
        from programs.utils import get_student_balance_data

        due_date = date(2026, 8, 1)
        Fee.objects.create(
            program=self.program,
            name="Registration Fee",
            amount=100.00,
            due_date=due_date,
        )
        balance_data = get_student_balance_data(self.student, self.program)
        fee_entry = next(e for e in balance_data["entries"] if e["type"] == "Fee")
        self.assertEqual(fee_entry.get("due_date"), due_date)

    def test_fee_added_email_contains_due_date(self):
        from programs.models import Adult, AdultStudentRelationship

        parent = Adult.objects.create(
            personal_email="parent@example.com", email_updates=True, is_parent=True
        )
        AdultStudentRelationship.objects.create(
            adult=parent, student=self.student, relationship_to_student="parent"
        )
        due_date = date(2026, 8, 1)
        Fee.objects.create(
            program=self.program,
            name="Registration Fee",
            amount=100.00,
            due_date=due_date,
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Due Date:", mail.outbox[0].body)
        self.assertIn("Aug. 1, 2026", mail.outbox[0].body)

    def test_payment_notification_email_contains_due_date(self):
        from programs.models import Adult, AdultStudentRelationship, Payment

        parent = Adult.objects.create(
            personal_email="parent@example.com", email_updates=True, is_parent=True
        )
        AdultStudentRelationship.objects.create(
            adult=parent, student=self.student, relationship_to_student="parent"
        )
        due_date = date(2026, 8, 1)
        Fee.objects.create(
            program=self.program,
            name="Registration Fee",
            amount=100.00,
            due_date=due_date,
        )
        mail.outbox = []
        Payment.objects.create(
            student=self.student,
            program=self.program,
            amount=50.00,
            paid_on=date.today(),
            paid_via="cash",
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Registration Fee", mail.outbox[0].body)
        self.assertIn("Due: Aug. 1, 2026", mail.outbox[0].body)


class FeeEffectiveDateTest(TestCase):
    def test_fee_effective_date_field(self):
        program = Program.objects.create(name="Test Program")
        effective_date = date(2026, 7, 1)
        fee = Fee.objects.create(
            program=program,
            name="Test Fee",
            amount=50.00,
            effective_date=effective_date,
        )
        self.assertEqual(fee.effective_date, effective_date)


class UtilsAndModelEdgeTests(TestCase):
    def test_compute_sliding_discount_rounding(self):
        self.assertEqual(
            compute_sliding_discount_rounded(Decimal("100.49"), Decimal("10")),
            Decimal("10"),
        )
        self.assertEqual(
            compute_sliding_discount_rounded(Decimal("100.50"), Decimal("10")),
            Decimal("10"),
        )
        self.assertEqual(
            compute_sliding_discount_rounded(Decimal("105.51"), Decimal("10")),
            Decimal("11"),
        )
        self.assertEqual(
            compute_sliding_discount_rounded(None, Decimal("10")), Decimal("0")
        )
        self.assertEqual(
            compute_sliding_discount_rounded(Decimal("100"), None), Decimal("0")
        )

    def test_student_eighteenth_birthday_regular_and_leap(self):
        s1 = Student.objects.create(
            legal_first_name="A",
            last_name="B",
            date_of_birth=date(2010, 5, 20),
        )
        self.assertEqual(s1.eighteenth_birthday(), date(2028, 5, 20))
        s2 = Student.objects.create(
            legal_first_name="C",
            last_name="D",
            date_of_birth=date(2008, 2, 29),
        )
        self.assertEqual(s2.eighteenth_birthday(), date(2026, 2, 28))

    def test_student_requires_background_check_sept1_rule(self):
        """A student needs clearances if they will be 17 on Sept 1 of the
        current academic year (which runs Sept 1 → Aug 31)."""
        from unittest.mock import patch

        from programs.models import timezone

        # Born 2009-09-02 → turns 17 on 2026-09-02, which is AFTER Sept 1 2026.
        # During the 2025-26 academic year (today before Sept 1 2026) they are
        # still 16 on Sept 1 2025, so they do NOT require clearances.
        s_born_sept2 = Student.objects.create(
            legal_first_name="A",
            last_name="B",
            date_of_birth=date(2009, 9, 2),
        )
        # Born 2009-09-01 → exactly 17 on Sept 1 2026, but only 16 on Sept 1
        # 2025, so during the 2025-26 academic year they do NOT require them yet.
        s_born_sept1 = Student.objects.create(
            legal_first_name="C",
            last_name="D",
            date_of_birth=date(2009, 9, 1),
        )
        # Born 2008-08-01 → 17 on Aug 1 2025 (before Sept 1 2025), requires them.
        s_older = Student.objects.create(
            legal_first_name="E",
            last_name="F",
            date_of_birth=date(2008, 8, 1),
        )
        with patch.object(timezone, "localdate", return_value=date(2026, 8, 1)):
            self.assertFalse(s_born_sept2.requires_background_check())
            self.assertFalse(s_born_sept1.requires_background_check())
            self.assertTrue(s_older.requires_background_check())

        # Once we cross Sept 1 2026, the academic year rolls forward: only
        # students 17 on Sept 1 2026 OR earlier require clearances.
        with patch.object(timezone, "localdate", return_value=date(2026, 9, 1)):
            # Born 2009-09-02 is still 16 on Sept 1 2026 → no check needed.
            self.assertFalse(s_born_sept2.requires_background_check())
            # Born 2009-09-01 is now exactly 17 on Sept 1 2026 → check needed.
            self.assertTrue(s_born_sept1.requires_background_check())

    def test_student_needs_background_check_requires_all_three(self):
        """needs_background_check() is True when a required student is missing
        at least one valid clearance of the three types."""
        from unittest.mock import patch

        from programs.models import BackgroundCheck, BackgroundCheckType, timezone

        student = Student.objects.create(
            legal_first_name="A",
            last_name="B",
            date_of_birth=date(2008, 8, 1),
        )
        with patch.object(timezone, "localdate", return_value=date(2026, 8, 1)):
            self.assertTrue(student.needs_background_check())
            # Provide all three valid clearances.
            for check_type in BackgroundCheckType.values:
                BackgroundCheck.objects.create(
                    student=student,
                    check_type=check_type,
                    cleared=True,
                    obtained_date=date(2022, 8, 1),
                )
            self.assertFalse(student.needs_background_check())
            # Expire one → needs again.
            expired = BackgroundCheck.objects.get(
                student=student, check_type=BackgroundCheckType.FBI
            )
            expired.obtained_date = date(2020, 1, 1)
            expired.save()
            self.assertTrue(student.needs_background_check())

    def test_sliding_scale_allows_multiple_records_per_student(self):
        prog = Program.objects.create(name="P")
        s = Student.objects.create(legal_first_name="A", last_name="B")
        Enrollment.objects.create(student=s, program=prog)
        SlidingScale.objects.create(
            student=s, percent=Decimal("10.0"), status=SlidingScale.STATUS_DECLINED
        )
        SlidingScale.objects.create(student=s, percent=Decimal("5.0"))
        self.assertEqual(SlidingScale.objects.filter(student=s).count(), 2)

    def test_fee_unique_together(self):
        from django.db import IntegrityError, transaction

        p1 = Program.objects.create(name="P1")
        p2 = Program.objects.create(name="P2")
        Fee.objects.create(program=p1, name="Dues", amount=Decimal("25.00"))
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Fee.objects.create(program=p1, name="Dues", amount=Decimal("30.00"))
        Fee.objects.create(program=p2, name="Dues", amount=Decimal("25.00"))


class FormBehaviorTests(TestCase):
    def setUp(self):
        self.program = Program.objects.create(name="Robotics")
        self.other_program = Program.objects.create(name="Art")
        self.enrolled_student = Student.objects.create(
            legal_first_name="Alex", last_name="Smith"
        )
        self.not_enrolled = Student.objects.create(
            legal_first_name="Casey", last_name="Doe"
        )
        Enrollment.objects.create(student=self.enrolled_student, program=self.program)
        self.fee_in_prog = Fee.objects.create(
            program=self.program, name="Dues", amount=Decimal("50.00")
        )
        self.fee_other = Fee.objects.create(
            program=self.other_program, name="Kit", amount=Decimal("20.00")
        )

    def test_payment_form_limits_queryset(self):
        form = PaymentForm(program=self.program)
        self.assertIn(self.enrolled_student, list(form.fields["student"].queryset))
        self.assertNotIn(self.not_enrolled, list(form.fields["student"].queryset))
        self.assertNotIn("fee", form.fields)

    def test_sliding_scale_form_percent_validation_and_queryset(self):
        form = SlidingScaleForm(
            program=self.program,
            data={
                "student": self.enrolled_student.pk,
                "percent": "150",
            },
        )
        self.assertFalse(form.is_valid())
        form2 = SlidingScaleForm(
            program=self.program,
            data={
                "student": self.enrolled_student.pk,
                "percent": "100",
            },
        )
        self.assertTrue(form2.is_valid(), form2.errors)
        self.assertIn(self.enrolled_student, list(form2.fields["student"].queryset))
        self.assertNotIn(self.not_enrolled, list(form2.fields["student"].queryset))

    @override_settings(
        EMAIL_SENDER_ACCOUNTS=[], DEFAULT_FROM_EMAIL="noreply@example.com"
    )
    def test_program_email_form_default_sender_without_accounts(self):
        form = ProgramEmailForm()
        choices = dict(form.fields["from_account"].choices)
        self.assertIn("DEFAULT", choices)
        self.assertIn("noreply@example.com", choices["DEFAULT"])

    @override_settings(
        EMAIL_SENDER_ACCOUNTS=[
            {"key": "ops", "email": "ops@example.com", "display_name": "Ops Team"},
            {"key": "info", "email": "info@example.com", "display_name": "Info Desk"},
        ]
    )
    def test_program_email_form_sender_accounts(self):
        form = ProgramEmailForm()
        choices = dict(form.fields["from_account"].choices)
        self.assertIn("ops", choices)
        self.assertIn("info", choices)
        p = Program.objects.create(name="X")
        form2 = ProgramEmailForm(program=p)
        self.assertTrue(hasattr(form2.fields["program"].widget, "input_type"))
        self.assertEqual(
            getattr(form2.fields["program"].widget, "input_type", ""), "hidden"
        )
        form_hidden_missing = ProgramEmailForm(
            program=p,
            data={
                "recipient_groups": ["students"],
                "subject": "Hello",
                "body": "Test",
                "from_account": "ops",
            },
        )
        self.assertFalse(form_hidden_missing.is_valid())
        self.assertIn("__all__", form_hidden_missing.errors)
        form_hidden_ok = ProgramEmailForm(
            program=p,
            data={
                "program": p.pk,
                "recipient_groups": ["students"],
                "subject": "Hello",
                "body": "Test",
                "from_account": "ops",
            },
        )
        self.assertTrue(form_hidden_ok.is_valid(), form_hidden_ok.errors)
