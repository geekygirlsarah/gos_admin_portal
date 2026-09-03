import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from programs.forms import StudentBalanceModelChoiceField
from programs.models import Enrollment, Fee, Program, SlidingScale, Student
from programs.utils import (
    get_active_sliding_scale,
    get_sliding_scale_for_program,
    get_student_balance_data,
    get_student_program_balance,
)

User = get_user_model()


class PastProgramSlidingScaleTest(TestCase):
    def setUp(self):
        password = "password"  # nosec B105
        self.user = User.objects.create_superuser(
            username="admin",
            password=password,
            email="admin@example.com",
        )
        self.client.login(username="admin", password=password)

    def test_past_program_sliding_scale_applies_discount(self):
        """A program in the past with an expired sliding scale active during the program

        must receive the sliding scale discount.
        """
        student = Student.objects.create(legal_first_name="Past", last_name="Student")
        program = Program.objects.create(
            name="Fall 2025 Program",
            start_date=datetime.date(2025, 9, 13),
            end_date=datetime.date(2025, 12, 21),
        )
        Enrollment.objects.create(student=student, program=program)
        Fee.objects.create(
            program=program,
            name="Tuition Fee",
            amount=Decimal("100.00"),
            effective_date=datetime.date(2025, 9, 1),
        )
        # Sliding scale was active during the 2025 program, but is expired today (in 2026)
        sliding = SlidingScale.objects.create(
            student=student,
            percent=Decimal("50.00"),
            date=datetime.date(2025, 8, 1),
            expiration_date=datetime.date(2025, 12, 31),
            status=SlidingScale.STATUS_APPROVED,
        )

        data = get_student_balance_data(student, program)
        self.assertEqual(data["total_fees"], Decimal("100.00"))
        self.assertEqual(data["total_sliding"], Decimal("50.00"))
        self.assertEqual(data["balance"], Decimal("50.00"))
        self.assertEqual(data["sliding_scale"], sliding)

        prog_balance = get_student_program_balance(student, program)
        self.assertEqual(prog_balance, Decimal("50.00"))

        # Check balance sheet view
        url = reverse("program_student_balance", args=[program.pk, student.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_sliding"], Decimal("50.00"))
        self.assertEqual(response.context["balance"], Decimal("50.00"))

    def test_multiple_historical_sliding_scales_scoped_to_correct_programs(self):
        """When a student has different sliding scales across years, each program

        receives the sliding scale corresponding to its timeframe.
        """
        student = Student.objects.create(
            legal_first_name="MultiYear", last_name="Student"
        )
        p2024 = Program.objects.create(
            name="2024 Program",
            start_date=datetime.date(2024, 9, 1),
            end_date=datetime.date(2024, 12, 20),
        )
        p2025 = Program.objects.create(
            name="2025 Program",
            start_date=datetime.date(2025, 9, 13),
            end_date=datetime.date(2025, 12, 21),
        )
        p2026 = Program.objects.create(
            name="2026 Program",
            start_date=datetime.date(2026, 9, 1),
            end_date=datetime.date(2026, 12, 20),
        )
        for prog in (p2024, p2025, p2026):
            Enrollment.objects.create(student=student, program=prog)
            Fee.objects.create(
                program=prog,
                name="Fee",
                amount=Decimal("100.00"),
                effective_date=prog.start_date,
            )

        SlidingScale.objects.create(
            student=student,
            percent=Decimal("60.00"),
            date=datetime.date(2024, 8, 1),
            expiration_date=datetime.date(2024, 12, 31),
            status=SlidingScale.STATUS_APPROVED,
        )
        SlidingScale.objects.create(
            student=student,
            percent=Decimal("40.00"),
            date=datetime.date(2025, 8, 1),
            expiration_date=datetime.date(2025, 12, 31),
            status=SlidingScale.STATUS_APPROVED,
        )
        SlidingScale.objects.create(
            student=student,
            percent=Decimal("20.00"),
            date=datetime.date(2026, 8, 1),
            expiration_date=datetime.date(2026, 12, 31),
            status=SlidingScale.STATUS_APPROVED,
        )

        data2024 = get_student_balance_data(student, p2024)
        data2025 = get_student_balance_data(student, p2025)
        data2026 = get_student_balance_data(student, p2026)

        self.assertEqual(data2024["total_sliding"], Decimal("60.00"))
        self.assertEqual(data2024["balance"], Decimal("40.00"))

        self.assertEqual(data2025["total_sliding"], Decimal("40.00"))
        self.assertEqual(data2025["balance"], Decimal("60.00"))

        self.assertEqual(data2026["total_sliding"], Decimal("20.00"))
        self.assertEqual(data2026["balance"], Decimal("80.00"))

    def test_mid_program_job_loss_sliding_scale_partial_discount(self):
        """When sliding scale starts mid-program, only fees on/after effective date

        receive the discount.
        """
        student = Student.objects.create(
            legal_first_name="JobLoss", last_name="Student"
        )
        program = Program.objects.create(
            name="Fall 2025 Program",
            start_date=datetime.date(2025, 9, 1),
            end_date=datetime.date(2025, 12, 31),
        )
        Enrollment.objects.create(student=student, program=program)
        Fee.objects.create(
            program=program,
            name="Early Fee",
            amount=Decimal("100.00"),
            effective_date=datetime.date(2025, 9, 1),
        )
        Fee.objects.create(
            program=program,
            name="Late Fee",
            amount=Decimal("100.00"),
            effective_date=datetime.date(2025, 11, 1),
        )
        # Applied in October after parent job loss
        SlidingScale.objects.create(
            student=student,
            percent=Decimal("50.00"),
            date=datetime.date(2025, 10, 15),
            expiration_date=datetime.date(2025, 12, 31),
            status=SlidingScale.STATUS_APPROVED,
        )

        data = get_student_balance_data(student, program)
        self.assertEqual(data["total_fees"], Decimal("200.00"))
        # Only Late Fee ($100) gets 50% discount = $50
        self.assertEqual(data["total_sliding"], Decimal("50.00"))
        self.assertEqual(data["balance"], Decimal("150.00"))

    def test_program_without_start_end_dates_uses_fee_dates(self):
        """If program start/end dates are None, fee dates are used to find the

        matching historical sliding scale.
        """
        student = Student.objects.create(legal_first_name="NoDate", last_name="Student")
        program = Program.objects.create(
            name="Undated Program",
            start_date=None,
            end_date=None,
        )
        Enrollment.objects.create(student=student, program=program)
        Fee.objects.create(
            program=program,
            name="Fee",
            amount=Decimal("100.00"),
            effective_date=datetime.date(2025, 9, 1),
        )
        SlidingScale.objects.create(
            student=student,
            percent=Decimal("50.00"),
            date=datetime.date(2025, 8, 1),
            expiration_date=datetime.date(2025, 12, 31),
            status=SlidingScale.STATUS_APPROVED,
        )

        data = get_student_balance_data(student, program)
        self.assertEqual(data["total_sliding"], Decimal("50.00"))
        self.assertEqual(data["balance"], Decimal("50.00"))

    def test_student_balance_model_choice_field_label(self):
        """StudentBalanceModelChoiceField properly reflects program balance with historical sliding scale."""
        student = Student.objects.create(legal_first_name="Choice", last_name="Student")
        program = Program.objects.create(
            name="Choice Program",
            start_date=datetime.date(2025, 9, 1),
            end_date=datetime.date(2025, 12, 31),
        )
        Enrollment.objects.create(student=student, program=program)
        Fee.objects.create(
            program=program,
            name="Fee",
            amount=Decimal("100.00"),
            effective_date=datetime.date(2025, 9, 1),
        )
        SlidingScale.objects.create(
            student=student,
            percent=Decimal("50.00"),
            date=datetime.date(2025, 8, 1),
            expiration_date=datetime.date(2025, 12, 31),
            status=SlidingScale.STATUS_APPROVED,
        )
        field = StudentBalanceModelChoiceField(
            queryset=Student.objects.all(), program=program
        )
        label = field.label_from_instance(student)
        self.assertEqual(label, "Choice Student ($50.00)")

    def test_get_sliding_scale_for_program_and_active_helpers(self):
        """Test get_sliding_scale_for_program and get_active_sliding_scale helper behavior."""
        student = Student.objects.create(legal_first_name="Helper", last_name="Student")
        program = Program.objects.create(
            name="Helper Program",
            start_date=datetime.date(2025, 9, 1),
            end_date=datetime.date(2025, 12, 31),
        )
        # None cases
        self.assertIsNone(get_sliding_scale_for_program(None, program))
        self.assertIsNone(get_sliding_scale_for_program(student, None))
        self.assertIsNone(get_active_sliding_scale(None))

        # Create approved sliding scale in 2025
        scale_2025 = SlidingScale.objects.create(
            student=student,
            percent=Decimal("50.00"),
            date=datetime.date(2025, 8, 1),
            expiration_date=datetime.date(2025, 12, 31),
            status=SlidingScale.STATUS_APPROVED,
        )
        matched = get_sliding_scale_for_program(student, program)
        self.assertEqual(matched, scale_2025)

        # Active on date checks
        self.assertEqual(
            get_active_sliding_scale(student, on_date=datetime.date(2025, 9, 1)),
            scale_2025,
        )
        self.assertIsNone(
            get_active_sliding_scale(student, on_date=datetime.date(2026, 1, 1))
        )
