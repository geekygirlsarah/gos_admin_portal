"""Tests for overlapping sliding scale detection and UI indicators across
the review queue, decision screen, and student balance sheets.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.test import TestCase, override_settings
from django.urls import reverse

from programs.models import Enrollment, Fee, Program, SlidingScale, Student
from programs.utils.balances import get_student_balance_data


@override_settings(FILE_ENCRYPTION_KEY="ZmDfcTF7_60GrrY167zsiPd67pEvs0aGOv2oasOM1Pg=")
class OverlappingSlidingScaleUITests(TestCase):
    def setUp(self):
        # Create Lead Mentor user
        self.lead_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.lead_user = User.objects.create_user(
            username="leadmentor",
            email="lead@example.com",
            password="password",  # nosec B106
        )
        self.lead_user.groups.add(self.lead_group)

        # Create Program and Student
        self.program = Program.objects.create(
            name="Robotics 2026",
            active=True,
            start_date=datetime.date(2026, 1, 1),
            end_date=datetime.date(2026, 12, 31),
        )
        self.student = Student.objects.create(
            legal_first_name="Jane",
            last_name="Doe",
        )
        self.enrollment = Enrollment.objects.create(
            student=self.student,
            program=self.program,
            active=True,
        )
        self.fee = Fee.objects.create(
            program=self.program,
            name="Tuition",
            amount=Decimal("100.00"),
            effective_date=datetime.date(2026, 2, 1),
        )

    def test_get_student_balance_data_includes_overlapping_sliding_scales(self):
        # Create two approved overlapping sliding scales
        scale1 = SlidingScale.objects.create(
            student=self.student,
            percent=Decimal("30.00"),
            status=SlidingScale.STATUS_APPROVED,
            date=datetime.date(2026, 1, 1),
            expiration_date=datetime.date(2026, 12, 31),
        )
        scale2 = SlidingScale.objects.create(
            student=self.student,
            percent=Decimal("50.00"),
            status=SlidingScale.STATUS_APPROVED,
            date=datetime.date(2026, 6, 1),
            expiration_date=datetime.date(2026, 12, 31),
        )

        data = get_student_balance_data(self.student, self.program)
        self.assertEqual(data["sliding_scale"], scale2)
        self.assertEqual(data["overlapping_sliding_count"], 2)
        self.assertEqual(len(data["all_overlapping_sliding_scales"]), 2)
        self.assertIn(scale1, data["all_overlapping_sliding_scales"])
        self.assertIn(scale2, data["all_overlapping_sliding_scales"])

    def test_balance_sheet_view_renders_multiple_discounts_notice(self):
        # Create two approved overlapping sliding scales
        SlidingScale.objects.create(
            student=self.student,
            percent=Decimal("30.00"),
            status=SlidingScale.STATUS_APPROVED,
            date=datetime.date(2026, 1, 1),
            expiration_date=datetime.date(2026, 12, 31),
        )
        SlidingScale.objects.create(
            student=self.student,
            percent=Decimal("50.00"),
            status=SlidingScale.STATUS_APPROVED,
            date=datetime.date(2026, 6, 1),
            expiration_date=datetime.date(2026, 12, 31),
        )

        self.client.force_login(self.lead_user)
        url = reverse(
            "program_student_balance",
            kwargs={"pk": self.program.pk, "student_id": self.student.pk},
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Multiple Sliding Scale Discounts On File")
        self.assertContains(response, "2 approved sliding scale records exist")
        self.assertContains(response, "50.00% off")

    def test_balance_sheet_print_view_renders_multiple_discounts_footnote(self):
        SlidingScale.objects.create(
            student=self.student,
            percent=Decimal("30.00"),
            status=SlidingScale.STATUS_APPROVED,
            date=datetime.date(2026, 1, 1),
            expiration_date=datetime.date(2026, 12, 31),
        )
        SlidingScale.objects.create(
            student=self.student,
            percent=Decimal("50.00"),
            status=SlidingScale.STATUS_APPROVED,
            date=datetime.date(2026, 6, 1),
            expiration_date=datetime.date(2026, 12, 31),
        )

        self.client.force_login(self.lead_user)
        url = reverse(
            "program_student_balance_print",
            kwargs={"pk": self.program.pk, "student_id": self.student.pk},
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Multiple sliding scale records on file")

    def test_review_decision_view_shows_existing_approved_discount_warning(self):
        # Existing approved discount
        SlidingScale.objects.create(
            student=self.student,
            percent=Decimal("30.00"),
            status=SlidingScale.STATUS_APPROVED,
            date=datetime.date(2026, 1, 1),
            expiration_date=datetime.date(2026, 12, 31),
        )
        # Pending application for same student
        pending_scale = SlidingScale.objects.create(
            student=self.student,
            status=SlidingScale.STATUS_PENDING,
            family_size=4,
            adjusted_gross_income=Decimal("45000.00"),
        )

        self.client.force_login(self.lead_user)
        url = reverse("sliding_scale_review_decide", kwargs={"pk": pending_scale.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Existing Active Discount Detected")
        self.assertContains(response, "30.00% off")
        self.assertContains(response, "discounts will not stack")

    def test_review_queue_list_highlights_multiple_active_and_has_active(self):
        # Two active scales for student
        SlidingScale.objects.create(
            student=self.student,
            percent=Decimal("30.00"),
            status=SlidingScale.STATUS_APPROVED,
            date=datetime.date(2026, 1, 1),
            expiration_date=datetime.date(2026, 12, 31),
        )
        SlidingScale.objects.create(
            student=self.student,
            percent=Decimal("50.00"),
            status=SlidingScale.STATUS_APPROVED,
            date=datetime.date(2026, 6, 1),
            expiration_date=datetime.date(2026, 12, 31),
        )
        # Pending application for same student
        SlidingScale.objects.create(
            student=self.student,
            status=SlidingScale.STATUS_PENDING,
            family_size=4,
            adjusted_gross_income=Decimal("45000.00"),
        )

        self.client.force_login(self.lead_user)
        url = reverse("sliding_scale_review_list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Multiple Active")
        self.assertContains(response, "Has Active Scale")
