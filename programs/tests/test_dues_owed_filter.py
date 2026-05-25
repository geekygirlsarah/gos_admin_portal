import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils.crypto import get_random_string

from programs.models import Enrollment, Fee, FeeAssignment, Payment, Program, Student


class DuesOwedFilterTest(TestCase):
    def setUp(self):
        password = get_random_string(32)
        self.user = User.objects.create_superuser(
            username="admin", password=password, email="admin@example.com"
        )
        self.client.login(username="admin", password=password)
        self.program = Program.objects.create(name="Test Program")

    def test_filter_owed(self):
        # Student 1: Owes money
        student1 = Student.objects.create(legal_first_name="Owes", last_name="Money")
        Enrollment.objects.create(student=student1, program=self.program)
        fee1 = Fee.objects.create(
            program=self.program, name="Fee1", amount=Decimal("100.00")
        )
        FeeAssignment.objects.create(fee=fee1, student=student1)

        # Student 2: Balance 0
        student2 = Student.objects.create(legal_first_name="No", last_name="Debt")
        Enrollment.objects.create(student=student2, program=self.program)
        fee2 = Fee.objects.create(
            program=self.program, name="Fee2", amount=Decimal("50.00")
        )
        FeeAssignment.objects.create(fee=fee2, student=student2)
        Payment.objects.create(
            student=student2,
            program=self.program,
            amount=Decimal("50.00"),
            paid_on=datetime.date.today(),
        )

        url = reverse("program_dues_owed", args=[self.program.pk])

        # Test default (shows both)
        response = self.client.get(url)
        self.assertEqual(len(response.context["rows"]), 2)

        # Test filter (shows only owed)
        response = self.client.get(url + "?filter=owed")
        # print(f"Rows: {len(response.context['rows'])}")
        # for row in response.context['rows']:
        #     print(f"Student: {row['student'].legal_first_name}, Amount: {row['amount_owed']}")
        self.assertEqual(len(response.context["rows"]), 1)
        self.assertEqual(response.context["rows"][0]["student"], student1)
