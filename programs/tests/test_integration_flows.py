from __future__ import annotations

import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from programs.models import (
    Adult,
    Enrollment,
    Fee,
    Payment,
    Program,
    SlidingScale,
    Student,
)


class IntegrationFlowTests(TestCase):
    """
    High-level integration tests that simulate full user stories
    to catch regressions that span multiple views and signals.
    """

    def setUp(self):
        # Setup groups
        self.lead_mentor_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.parent_group, _ = Group.objects.get_or_create(name="Parent")

        # Setup users
        self.admin = User.objects.create_superuser(
            username="admin",
            password="password",
            email="admin@example.com",
        )  # nosec B101 B106
        self.admin.groups.add(
            self.lead_mentor_group
        )  # Explicitly add to group just in case

        self.parent_user = User.objects.create_user(
            username="parent",
            password="password",
            email="parent@example.com",
        )  # nosec B101 B106
        self.parent_user.groups.add(self.parent_group)

        self.parent_adult = Adult.objects.create(
            user=self.parent_user,
            first_name="Pat",
            last_name="Parent",
            personal_email="parent@example.com",
            is_parent=True,
        )

        self.student = Student.objects.create(
            legal_first_name="Ada",
            last_name="Lovelace",
            personal_email="ada@example.com",
        )
        self.student.adults.add(self.parent_adult)
        self.student.primary_contact = self.parent_adult
        self.student.save()

        self.program = Program.objects.create(
            name="Robotics 2026",
            start_date=datetime.date(2026, 1, 1),
            end_date=datetime.date(2026, 6, 1),
        )

        self.enrollment = Enrollment.objects.create(
            student=self.student, program=self.program
        )

        self.client.force_login(self.admin)

    def test_sliding_scale_to_balance_sheet_flow(self):
        """
        Story: Lead Mentor adds a fee, Parent applies for sliding scale,
        Lead Mentor approves it, and Balance Sheet is verified.
        """
        # 1. Lead Mentor adds a fee to the program
        fee_url = reverse("program_fee_create", args=[self.program.pk])

        response = self.client.post(
            fee_url,
            {
                "program": self.program.pk,
                "name": "Registration Fee",
                "amount": "200.00",
                "effective_date": "2026-01-01",
                "due_date": "2026-02-01",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            Fee.objects.filter(program=self.program, name="Registration Fee").exists()
        )

        # 2. Verify student balance sheet currently shows $200
        balance_url = reverse(
            "program_student_balance", args=[self.program.pk, self.student.pk]
        )
        response = self.client.get(balance_url)
        self.assertEqual(response.context["balance"], Decimal("200.00"))

        # 3. Parent applies for sliding scale
        # We'll simulate the Lead Mentor creating it for them to simplify the view flow,
        # but we could also use the parent's client.
        self.client.login(username="admin", password="password")  # nosec B106

        ss = SlidingScale.objects.create(
            student=self.student,
            percent=Decimal("50.00"),
            status=SlidingScale.STATUS_PENDING,
            date=datetime.date(2026, 1, 1),  # Match fee effective date
        )

        # 4. Lead Mentor reviews and approves it
        review_url = reverse("sliding_scale_review_decide", args=[ss.pk])
        response = self.client.post(
            review_url,
            {
                "action": "approve",
                "percent": "50.00",
                "date": "2026-01-01",
                "notes": "Approved for 50%",
            },
        )
        self.assertEqual(response.status_code, 302)

        ss.refresh_from_db()
        self.assertEqual(ss.status, SlidingScale.STATUS_APPROVED)

        # 5. Verify balance sheet now shows $100 (50% discount)
        response = self.client.get(balance_url)
        # The balance calculation: 200 fee - (200 * 0.50) = 100
        self.assertEqual(response.context["balance"], Decimal("100.00"))

        # 6. Record a partial payment
        payment_url = reverse("program_payment_create", args=[self.program.pk])
        response = self.client.post(
            payment_url,
            {
                "student": self.student.pk,
                "amount": "40.00",
                "paid_via": "check",
                "check_number": "1234",
                "paid_on": "2026-03-01",
            },
        )
        self.assertEqual(response.status_code, 302)

        # 7. Final balance check: 100 - 40 = 60
        response = self.client.get(balance_url)
        self.assertEqual(response.context["balance"], Decimal("60.00"))
        self.assertEqual(response.context["total_payments"], Decimal("40.00"))
        self.assertEqual(response.context["total_sliding"], Decimal("100.00"))
