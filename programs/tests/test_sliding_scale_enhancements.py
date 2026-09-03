import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from programs.models import SlidingScale, Student

User = get_user_model()


class SlidingScaleEnhancementsTests(TestCase):
    def setUp(self):
        self.lead_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.lead_user = User.objects.create_user(
            username="lead_user",
            email="lead@example.com",
            password="password",  # nosec B106
            first_name="Jane",
            last_name="Lead",
        )
        self.lead_user.groups.add(self.lead_group)

        self.student1 = Student.objects.create(
            legal_first_name="Alice",
            last_name="Smith",
        )
        self.student2 = Student.objects.create(
            legal_first_name="Bob",
            last_name="Jones",
        )
        self.student3 = Student.objects.create(
            legal_first_name="Charlie",
            last_name="Brown",
        )

    def _login_lead_with_permissions(self):
        for codename in ["add_slidingscale", "change_slidingscale"]:
            perm = Permission.objects.get(codename=codename)
            self.lead_user.user_permissions.add(perm)
        self.client.login(username="lead_user", password="password")  # nosec B106

    def test_manual_sliding_scale_creation_records_creator(self):
        """When a staff/lead manually creates a sliding scale, reviewed_by and reviewed_at are populated."""
        self._login_lead_with_permissions()
        url = reverse("sliding_scale_create")
        response = self.client.post(
            url,
            {
                "student": self.student1.pk,
                "family_size": 3,
                "adjusted_gross_income": "25000.00",
                "percent": "60.00",
                "date": "2026-01-01",
                "expiration_date": "2026-12-31",
            },
            follow=False,
        )
        self.assertEqual(response.status_code, 302)
        sliding = SlidingScale.objects.get(student=self.student1)
        self.assertEqual(sliding.reviewed_by, self.lead_user)
        self.assertIsNotNone(sliding.reviewed_at)

    def test_effective_dates_display_property(self):
        """SlidingScale model provides effective_dates_display helper."""
        s1 = SlidingScale(
            student=self.student1,
            date=datetime.date(2026, 1, 1),
            expiration_date=datetime.date(2026, 6, 30),
        )
        self.assertEqual(s1.effective_dates_display, "Jan 1, 2026 – Jun 30, 2026")

        s2 = SlidingScale(
            student=self.student1,
            date=datetime.date(2026, 9, 1),
            expiration_date=None,
        )
        self.assertEqual(s2.effective_dates_display, "From Sep 1, 2026")

        s3 = SlidingScale(
            student=self.student1,
            date=None,
            expiration_date=datetime.date(2026, 12, 31),
        )
        self.assertEqual(s3.effective_dates_display, "Through Dec 31, 2026")

        s4 = SlidingScale(
            student=self.student1,
            date=None,
            expiration_date=None,
        )
        self.assertEqual(s4.effective_dates_display, "No expiration")

    def test_review_list_view_active_and_past_sections(self):
        """Active sliding scales are listed with dates and creator, and past scales are grouped by school year in an accordion."""
        self._login_lead_with_permissions()

        today = timezone.localdate()

        # Active scale 1: recent start date, no expiration
        scale_active_1 = SlidingScale.objects.create(
            student=self.student1,
            percent=Decimal("50.00"),
            date=today - datetime.timedelta(days=30),
            expiration_date=None,
            status=SlidingScale.STATUS_APPROVED,
            reviewed_by=self.lead_user,
            reviewed_at=timezone.now(),
        )

        # Active scale 2: newer start date, future expiration date
        scale_active_2 = SlidingScale.objects.create(
            student=self.student2,
            percent=Decimal("75.00"),
            date=today,
            expiration_date=today + datetime.timedelta(days=180),
            status=SlidingScale.STATUS_APPROVED,
            reviewed_by=self.lead_user,
            reviewed_at=timezone.now(),
        )

        # Past scale 1 (expired last school year, e.g. 2024-2025)
        scale_past_1 = SlidingScale.objects.create(
            student=self.student1,
            percent=Decimal("40.00"),
            date=datetime.date(2024, 9, 1),
            expiration_date=datetime.date(2025, 5, 31),
            status=SlidingScale.STATUS_APPROVED,
            reviewed_by=self.lead_user,
            reviewed_at=timezone.now(),
        )

        # Past scale 2 (expired two school years ago, e.g. 2023-2024)
        scale_past_2 = SlidingScale.objects.create(
            student=self.student3,
            percent=Decimal("30.00"),
            date=datetime.date(2023, 9, 1),
            expiration_date=datetime.date(2024, 5, 31),
            status=SlidingScale.STATUS_APPROVED,
            reviewed_by=self.lead_user,
            reviewed_at=timezone.now(),
        )

        # Declined scale
        scale_declined = SlidingScale.objects.create(
            student=self.student3,
            date=datetime.date(2024, 10, 1),
            status=SlidingScale.STATUS_DECLINED,
            decline_reason="Income exceeded threshold",
            reviewed_by=self.lead_user,
            reviewed_at=timezone.now(),
        )

        response = self.client.get(reverse("sliding_scale_review_list"))
        self.assertEqual(response.status_code, 200)

        # Headers and labels
        self.assertContains(response, "Active Sliding Scales")
        self.assertContains(response, "Effective Dates")
        self.assertContains(response, "Added / Reviewed By")

        # Context checks
        active_apps = response.context["active_applications"]
        self.assertEqual(len(active_apps), 2)
        # Should be ordered by most recent dates first (scale_active_2 before scale_active_1)
        self.assertEqual(active_apps[0], scale_active_2)
        self.assertEqual(active_apps[1], scale_active_1)

        past_by_year = dict(response.context["past_applications_by_year"])
        self.assertIn("2024-2025", past_by_year)
        self.assertIn("2023-2024", past_by_year)
        self.assertIn(scale_past_1, past_by_year["2024-2025"])
        self.assertIn(scale_declined, past_by_year["2024-2025"])
        self.assertIn(scale_past_2, past_by_year["2023-2024"])

        # Template contains past accordion and school year labels
        self.assertContains(response, "Past Sliding Scales")
        self.assertContains(response, "2024-2025 School Year")
        self.assertContains(response, "2023-2024 School Year")
