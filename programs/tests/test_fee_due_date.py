from datetime import date

from django.test import TestCase

from programs.models import Enrollment, Fee, Program, Student
from programs.utils import get_student_balance_data


class FeeDueDateTest(TestCase):
    def setUp(self):
        self.program = Program.objects.create(name="Test Program")
        self.student = Student.objects.create(first_name="John", last_name="Doe")
        Enrollment.objects.create(student=self.student, program=self.program)

    def test_fee_due_date_field(self):
        # This will fail initially because due_date does not exist
        due_date = date(2026, 8, 1)
        fee = Fee.objects.create(
            program=self.program,
            name="Registration Fee",
            amount=100.00,
            due_date=due_date,
        )
        self.assertEqual(fee.due_date, due_date)

    def test_fee_due_date_in_balance_data(self):
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
        from django.core import mail

        from programs.models import Adult, AdultStudentRelationship

        # Create a parent with email updates enabled
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
        # Django template date format might vary, but "Aug. 1, 2026" is what we saw
        self.assertIn("Due Date:", mail.outbox[0].body)
        self.assertIn("Aug. 1, 2026", mail.outbox[0].body)

    def test_payment_notification_email_contains_due_date(self):
        from django.core import mail

        from programs.models import Adult, AdultStudentRelationship, Payment

        # Create a parent with email updates enabled
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

        # Clear outbox (from fee creation)
        mail.outbox = []

        Payment.objects.create(
            student=self.student,
            program=self.program,
            amount=50.00,
            paid_on=date.today(),
            paid_via="cash",
        )

        self.assertEqual(len(mail.outbox), 1)
        # Check if the fee and its due date are listed in the payment notification
        self.assertIn("Registration Fee", mail.outbox[0].body)
        self.assertIn("Due: Aug. 1, 2026", mail.outbox[0].body)
