from decimal import Decimal

from django.contrib.auth.models import Permission, User
from django.test import Client, TestCase
from django.urls import reverse

from programs.models import Adult, Enrollment, Fee, Program, Student


class EmailBalancesTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(username="admin")
        self.client = Client()
        self.client.force_login(self.user)

        self.program = Program.objects.create(
            name="Test Program", year=2024, active=True
        )

        # Student 1: Zero balance
        self.s1 = Student.objects.create(first_name="Alice", last_name="Alpha")
        Enrollment.objects.create(student=self.s1, program=self.program)
        self.a1 = Adult.objects.create(
            first_name="P1",
            last_name="A1",
            personal_email="a1@example.com",
            email_updates=True,
        )
        self.s1.primary_contact = self.a1
        self.s1.save()

        # Student 2: Positive balance
        self.s2 = Student.objects.create(first_name="Bob", last_name="Beta")
        Enrollment.objects.create(student=self.s2, program=self.program)
        self.a2 = Adult.objects.create(
            first_name="P2",
            last_name="A2",
            personal_email="a2@example.com",
            email_updates=True,
        )
        self.s2.primary_contact = self.a2
        self.s2.save()
        f1 = Fee.objects.create(
            program=self.program, name="Fee 1", amount=Decimal("100.00")
        )
        from programs.models import FeeAssignment

        FeeAssignment.objects.create(fee=f1, student=self.s2)

        # Student 3: Negative balance (if possible, let's just use zero and positive for now)
        # Actually, let's make one with a payment more than fee
        self.s3 = Student.objects.create(first_name="Charlie", last_name="Gamma")
        Enrollment.objects.create(student=self.s3, program=self.program)
        self.a3 = Adult.objects.create(
            first_name="P3",
            last_name="A3",
            personal_email="a3@example.com",
            email_updates=True,
        )
        self.s3.primary_contact = self.a3
        self.s3.save()
        # s3 has no fees, but if we add a payment it would be negative.
        # But wait, balance = total_fees - total_sliding - total_payments.
        # If total_fees=0 and total_payments=50, balance = -50.
        from django.utils import timezone

        from programs.models import Payment

        Payment.objects.create(
            student=self.s3,
            program=self.program,
            amount=Decimal("50.00"),
            paid_via="cash",
            paid_on=timezone.now().date(),
        )

    def test_view_get(self):
        url = reverse("program_dues_email", args=[self.program.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Email Balance Sheets")

    def test_view_post_all(self):
        url = reverse("program_dues_email", args=[self.program.id])
        data = {
            "program": self.program.id,
            "subject": "Test Subject",
            "recipient_filter": "all",
            "from_account": "DEFAULT",
        }
        # In current implementation, post actually sends emails and redirects if successful.
        # It redirects to 'program_dues_owed' on success.
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(
            response, reverse("program_dues_owed", args=[self.program.id])
        )

    def test_view_post_positive(self):
        url = reverse("program_dues_email", args=[self.program.id])
        data = {
            "program": self.program.id,
            "subject": "Test Subject",
            "recipient_filter": "positive",
            "from_account": "DEFAULT",
        }
        # Bob (s2) has positive balance (100.00)
        # Alice (s1) has zero balance
        # Charlie (s3) has negative balance (-50.00)
        # Only Bob should be in targets.
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)

    def test_view_post_individual(self):
        url = reverse("program_dues_email", args=[self.program.id])
        data = {
            "program": self.program.id,
            "subject": "Test Subject",
            "recipient_filter": "individual",
            "student": self.s1.id,
            "from_account": "DEFAULT",
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)

    def test_student_dropdown_labels_with_balances(self):
        url = reverse("program_dues_email", args=[self.program.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        # print(response.content.decode()) # Debug
        # print("DEBUG: s1 name:", self.s1.first_name, self.s1.last_name)
        # print("DEBUG: labels:", [self.s1.first_name, self.s1.last_name])

        # Expected balances:
        # s1 (Alice Alpha): 0.00
        # s2 (Bob Beta): 100.00
        # s3 (Charlie Gamma): -50.00

        self.assertContains(response, "Alice Alpha ($0.00)")
        self.assertContains(response, "Bob Beta ($100.00)")
        self.assertContains(response, "Charlie Gamma ($-50.00)")
