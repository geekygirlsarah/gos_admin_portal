"""Inactive students should not receive fee-added notifications."""

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase

from programs.models import Adult, AdultStudentRelationship, Enrollment, Fee, Program, Student


class InactiveStudentFeeNotificationTests(TestCase):
    def setUp(self):
        self.program = Program.objects.create(name="Test Program", active=True)
        parent_user = User.objects.create_user(
            username="parent", password="password"
        )  # nosec B106
        self.parent = Adult.objects.create(
            first_name="Pat",
            last_name="Parent",
            is_parent=True,
            login_enabled=True,
            email_updates=True,
            personal_email="parent@example.com",
            user=parent_user,
        )
        self.student = Student.objects.create(
            first_name="Sam",
            last_name="Student",
            date_of_birth="2010-01-01",
        )
        AdultStudentRelationship.objects.create(adult=self.parent, student=self.student)
        self.enrollment = Enrollment.objects.create(
            student=self.student, program=self.program
        )
        mail.outbox = []

    def _create_fee(self):
        return Fee.objects.create(program=self.program, name="Test Fee", amount=100)

    def test_graduated_student_gets_no_fee_email(self):
        self.student.graduated = True
        self.student.save()
        mail.outbox = []
        self._create_fee()
        self.assertEqual(len(mail.outbox), 0)

    def test_deactivated_enrollment_student_gets_no_fee_email(self):
        self.enrollment.active = False
        self.enrollment.save()
        mail.outbox = []
        self._create_fee()
        self.assertEqual(len(mail.outbox), 0)

    def test_active_student_still_gets_fee_email(self):
        self._create_fee()
        self.assertEqual(len(mail.outbox), 1)
