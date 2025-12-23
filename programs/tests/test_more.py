import datetime
from decimal import Decimal

from django.test import TestCase, override_settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from programs.models import (
    Program,
    Student,
    SlidingScale,
    Fee,
    Enrollment,
    RaceEthnicity,
    StudentApplication,
    School,
)
from programs.forms import PaymentForm, SlidingScaleForm, ProgramEmailForm
from programs.views import compute_sliding_discount_rounded


class UtilsAndModelEdgeTests(TestCase):
    def test_compute_sliding_discount_rounding(self):
        # 10% of 100.49 -> 10.049 -> 10.05 -> rounds to 10 (half-down at .50 rounds down)
        self.assertEqual(
            compute_sliding_discount_rounded(Decimal("100.49"), Decimal("10")),
            Decimal("10"),
        )
        # 10% of 100.50 -> 10.05 -> HALF_DOWN => 10
        self.assertEqual(
            compute_sliding_discount_rounded(Decimal("100.50"), Decimal("10")),
            Decimal("10"),
        )
        # 10% of 105.51 -> 10.551 -> rounds to 11 (>.50)
        self.assertEqual(
            compute_sliding_discount_rounded(Decimal("105.51"), Decimal("10")),
            Decimal("11"),
        )
        # Guard rails for None/invalid
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
            date_of_birth=datetime.date(2010, 5, 20),
        )
        self.assertEqual(s1.eighteenth_birthday(), datetime.date(2028, 5, 20))
        # Feb 29 should map to Feb 28 on non-leap year
        s2 = Student.objects.create(
            legal_first_name="C",
            last_name="D",
            date_of_birth=datetime.date(2008, 2, 29),
        )
        self.assertEqual(s2.eighteenth_birthday(), datetime.date(2026, 2, 28))

    def test_student_requires_background_check(self):
        # Student turns 18 on 2028-05-20
        s = Student.objects.create(
            legal_first_name="A",
            last_name="B",
            date_of_birth=datetime.date(2010, 5, 20),
        )
        prog = Program.objects.create(
            name="Season",
            start_date=datetime.date(2028, 1, 1),
            end_date=datetime.date(2028, 12, 31),
        )
        self.assertTrue(s.requires_background_check(prog))
        # If program lacks dates -> False
        p2 = Program.objects.create(name="No Dates")
        self.assertFalse(s.requires_background_check(p2))
        # If program ends before 18th -> False
        p3 = Program.objects.create(
            name="Early",
            start_date=datetime.date(2028, 1, 1),
            end_date=datetime.date(2028, 5, 19),
        )
        self.assertFalse(s.requires_background_check(p3))
        # If program is after 18th but start <= end (always) -> True (because end >= birthday)
        p4 = Program.objects.create(
            name="After",
            start_date=datetime.date(2028, 5, 21),
            end_date=datetime.date(2028, 6, 1),
        )
        self.assertTrue(s.requires_background_check(p4))

    def test_sliding_scale_unique_together(self):
        prog = Program.objects.create(name="P")
        s = Student.objects.create(legal_first_name="A", last_name="B")
        Enrollment.objects.create(student=s, program=prog)
        SlidingScale.objects.create(student=s, program=prog, percent=Decimal("10.0"))
        with self.assertRaises(Exception):
            SlidingScale.objects.create(student=s, program=prog, percent=Decimal("5.0"))

    def test_fee_unique_together(self):
        p1 = Program.objects.create(name="P1")
        p2 = Program.objects.create(name="P2")
        Fee.objects.create(program=p1, name="Dues", amount=Decimal("25.00"))
        # Same name in same program should fail (wrap in atomic to avoid breaking the outer transaction)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Fee.objects.create(program=p1, name="Dues", amount=Decimal("30.00"))
        # Same name across different programs is ok
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
        # Student choices include only enrolled
        self.assertIn(self.enrolled_student, list(form.fields["student"].queryset))
        self.assertNotIn(self.not_enrolled, list(form.fields["student"].queryset))
        # Fee choices include only fees for program
        self.assertIn(self.fee_in_prog, list(form.fields["fee"].queryset))
        self.assertNotIn(self.fee_other, list(form.fields["fee"].queryset))

    def test_sliding_scale_form_percent_validation_and_queryset(self):
        form = SlidingScaleForm(
            program=self.program,
            data={
                "student": self.enrolled_student.pk,
                "percent": "150",  # invalid (>100)
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
        # queryset restriction
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
        # When program is passed, it should be hidden and required
        p = Program.objects.create(name="X")
        form2 = ProgramEmailForm(program=p)
        self.assertTrue(hasattr(form2.fields["program"].widget, "input_type"))
        self.assertEqual(
            getattr(form2.fields["program"].widget, "input_type", ""), "hidden"
        )
        # If hidden and not provided in data -> non-field error
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
        # If provided -> valid (assuming minimal required fields)
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


class ApplicationFlowTests(TestCase):
    def setUp(self):
        self.program = Program.objects.create(name="Season 25")
        self.school = School.objects.create(name="North High")
        # Ensure race options exist
        for key, name in [
            ("black-or-african-american", "Black or African-American"),
            ("hispanic-or-latino", "Hispanic or Latino"),
            ("asian", "Asian"),
            ("white", "White"),
            ("other", "Other"),
        ]:
            RaceEthnicity.objects.get_or_create(key=key, defaults={"name": name})

    def test_student_application_approve_creates_and_enrolls(self):
        app = StudentApplication.objects.create(
            program=self.program,
            legal_first_name="Jamie",
            first_name="Jay",
            last_name="Lee",
            personal_email="jay@example.com",
            school=self.school,
            race_ethnicity="Black, Asian",
        )
        student = app.approve()
        # Student created
        self.assertIsNotNone(student.pk)
        self.assertTrue(
            Enrollment.objects.filter(student=student, program=self.program).exists()
        )
        # Status updated
        app.refresh_from_db()
        self.assertEqual(app.status, "accepted")
        # Race mapping set
        keys = set(student.race_ethnicities.values_list("key", flat=True))
        self.assertTrue({"black-or-african-american", "asian"}.issubset(keys))

    def test_student_application_approve_reuses_existing_by_email(self):
        existing = Student.objects.create(
            legal_first_name="Existing",
            last_name="Person",
            personal_email="e@example.com",
        )
        app = StudentApplication.objects.create(
            program=self.program,
            legal_first_name="New",
            last_name="Name",
            personal_email="e@example.com",
        )
        student = app.approve()
        self.assertEqual(student.pk, existing.pk)
        self.assertTrue(
            Enrollment.objects.filter(student=existing, program=self.program).exists()
        )
