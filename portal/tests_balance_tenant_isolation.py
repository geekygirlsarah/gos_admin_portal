import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db.models.signals import post_init
from django.http import Http404
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from GoSAdminPortal.test_runner import _assign_default_test_org
from organizations.models import Organization, UserOrganizationMembership
from portal.views import DashboardView, ParentPaymentsView
from programs.models import (
    Adult,
    AdultStudentRelationship,
    Enrollment,
    Fee,
    FeeAssignment,
    Payment,
    Program,
    SlidingScale,
    Student,
)


@override_settings(
    ALLOWED_HOSTS=[".localhost", "testserver"],
    MOMENTUM_ROOT_DOMAIN="localhost",
)
class ParentBalanceTenantIsolationTests(TestCase):
    tenant_models = (
        Adult,
        AdultStudentRelationship,
        Enrollment,
        Fee,
        FeeAssignment,
        Payment,
        Program,
        SlidingScale,
        Student,
    )

    def setUp(self):
        self.disconnected_models = []
        for model in self.tenant_models:
            if post_init.disconnect(
                receiver=_assign_default_test_org,
                sender=model,
                dispatch_uid=f"default-test-org-{model._meta.label}",
            ):
                self.disconnected_models.append(model)
        self.addCleanup(self._restore_default_organization_hook)

        User = get_user_model()
        self.alpha = Organization.objects.create(
            name="Balance Alpha",
            slug="parent-balance-alpha",
            subdomain="parent-balance-alpha",
        )
        self.beta = Organization.objects.create(
            name="Balance Beta",
            slug="parent-balance-beta",
            subdomain="parent-balance-beta",
        )
        self.alpha_host = "parent-balance-alpha.localhost"
        self.parent_user = User.objects.create_user(
            username="parent-balance-user", password="password123"  # nosec B106
        )
        self.parent = Adult.objects.create(
            user=self.parent_user,
            organization=self.alpha,
            legal_first_name="Pat",
            last_name="Parent",
            is_parent=True,
        )
        self.student = Student.objects.create(
            organization=self.alpha,
            legal_first_name="Local",
            last_name="Child",
        )
        AdultStudentRelationship.objects.create(
            organization=self.alpha,
            adult=self.parent,
            student=self.student,
            relationship_to_student="parent",
        )
        UserOrganizationMembership.objects.update_or_create(
            user=self.parent_user,
            organization=self.alpha,
            role="Parent",
            defaults={"is_active": True},
        )
        UserOrganizationMembership.objects.update_or_create(
            user=self.parent_user,
            organization=Organization.objects.get(slug="gos"),
            role="Parent",
            defaults={"is_active": True},
        )

        today = timezone.localdate()
        self.program = Program.objects.create(
            organization=self.alpha,
            name="Local Program",
            active=True,
            start_date=today - datetime.timedelta(days=1),
            end_date=today + datetime.timedelta(days=1),
        )
        self.enrollment = Enrollment.objects.create(
            organization=self.alpha,
            student=self.student,
            program=self.program,
            active=True,
        )
        self.fee = Fee.objects.create(
            organization=self.alpha,
            program=self.program,
            name="Local dues",
            amount=Decimal("100.00"),
            effective_date=today,
        )
        self.payment = Payment.objects.create(
            organization=self.alpha,
            student=self.student,
            program=self.program,
            amount=Decimal("25.00"),
            paid_on=today,
            paid_via="cash",
        )

    def _restore_default_organization_hook(self):
        for model in self.disconnected_models:
            post_init.connect(
                receiver=_assign_default_test_org,
                sender=model,
                dispatch_uid=f"default-test-org-{model._meta.label}",
            )

    def _payments(self):
        self.client.force_login(self.parent_user)
        return self.client.get(reverse("parent_payments"), HTTP_HOST=self.alpha_host)

    def _dashboard(self):
        self.client.force_login(self.parent_user)
        return self.client.get(reverse("profile_dashboard"), HTTP_HOST=self.alpha_host)

    def assert_balance_unavailable_on_both_parent_screens(self):
        payments = self._payments()
        self.assertTrue(payments.context["family_balance_unavailable"])
        self.assertIsNone(payments.context["grand_total"])
        student_row = payments.context["student_rows"][0]
        self.assertTrue(student_row["balance_unavailable"])
        self.assertIsNone(student_row["student_total_owed"])
        self.assertContains(payments, "Family balance unavailable")
        self.assertContains(payments, "Balance unavailable")
        self.assertNotContains(payments, "Paid in Full")
        self.assertNotContains(payments, "Total Due: $")

        dashboard = self._dashboard()
        self.assertTrue(dashboard.context["family_balance_unavailable"])
        self.assertIsNone(dashboard.context["family_total_owed"])
        row = dashboard.context["parent_data"][0]["active_rows"][0]
        self.assertTrue(row["balance_unavailable"])
        self.assertIsNone(row["balance"])
        self.assertContains(dashboard, "Family balance unavailable")
        self.assertContains(dashboard, "Balance unavailable")
        self.assertNotContains(dashboard, "Paid in full")
        self.assertNotContains(dashboard, "$75.00")

    def test_local_balances_remain_available_and_family_totals_are_complete(self):
        payments = self._payments()
        self.assertEqual(payments.context["grand_total"], Decimal("75.00"))
        self.assertContains(payments, "Total Due: $75.00")

        dashboard = self._dashboard()
        self.assertEqual(dashboard.context["family_total_owed"], Decimal("75.00"))
        self.assertFalse(dashboard.context["family_balance_unavailable"])
        self.assertContains(dashboard, "$75.00")

    def test_foreign_child_and_enrollment_are_not_shown_in_parent_payments(self):
        foreign_student = Student.objects.create(
            organization=self.beta,
            legal_first_name="Foreign",
            last_name="Child",
        )
        foreign_program = Program.objects.create(
            organization=self.beta,
            name="Foreign Program",
            active=True,
        )
        AdultStudentRelationship.objects.create(
            organization=self.alpha,
            adult=self.parent,
            student=foreign_student,
            relationship_to_student="parent",
        )
        Enrollment.objects.create(
            organization=self.beta,
            student=foreign_student,
            program=foreign_program,
            active=True,
        )
        Fee.objects.create(
            organization=self.beta,
            program=foreign_program,
            name="Foreign dues",
            amount=Decimal("300.00"),
        )

        payments = self._payments()
        self.assertContains(payments, "Local Child")
        self.assertNotContains(payments, "Foreign Child")
        self.assertNotContains(payments, "Foreign Program")

        dashboard = self._dashboard()
        self.assertContains(dashboard, "Local Child")
        self.assertNotContains(dashboard, "Foreign Child")
        self.assertNotContains(dashboard, "Foreign Program")

    def test_foreign_owned_fee_makes_parent_statements_unavailable(self):
        Fee.objects.create(
            organization=self.beta,
            program=self.program,
            name="Foreign-owned dues",
            amount=Decimal("50.00"),
            effective_date=timezone.localdate(),
        )

        self.assert_balance_unavailable_on_both_parent_screens()

    def test_foreign_owned_assignment_makes_parent_statements_unavailable(self):
        FeeAssignment.objects.create(
            organization=self.beta,
            fee=self.fee,
            student=self.student,
        )

        self.assert_balance_unavailable_on_both_parent_screens()

    def test_foreign_owned_payment_makes_parent_statements_unavailable(self):
        Payment.objects.create(
            organization=self.beta,
            student=self.student,
            program=self.program,
            amount=Decimal("10.00"),
            paid_on=timezone.localdate(),
            paid_via="cash",
        )

        self.assert_balance_unavailable_on_both_parent_screens()

    def test_foreign_approved_sliding_scale_makes_parent_statements_unavailable(self):
        SlidingScale.objects.create(
            organization=self.beta,
            student=self.student,
            status=SlidingScale.STATUS_APPROVED,
            percent=Decimal("50.00"),
        )

        self.assert_balance_unavailable_on_both_parent_screens()

    def test_foreign_pending_sliding_scale_is_not_exposed(self):
        SlidingScale.objects.create(
            organization=self.beta,
            student=self.student,
            status=SlidingScale.STATUS_PENDING,
            family_size=3,
            adjusted_gross_income=Decimal("25000.00"),
        )

        response = self._payments()

        self.assertNotContains(response, "Application pending review")
        self.assertNotContains(response, "Withdraw Application")

    def test_local_pending_sliding_scale_remains_visible(self):
        SlidingScale.objects.create(
            organization=self.alpha,
            student=self.student,
            status=SlidingScale.STATUS_PENDING,
            family_size=3,
            adjusted_gross_income=Decimal("25000.00"),
        )

        response = self._payments()

        self.assertContains(response, "Application pending review")
        self.assertContains(response, "Withdraw Application")

    def test_unsafe_student_suppresses_partial_family_total(self):
        sibling = Student.objects.create(
            organization=self.alpha,
            legal_first_name="Sam",
            last_name="Sibling",
        )
        AdultStudentRelationship.objects.create(
            organization=self.alpha,
            adult=self.parent,
            student=sibling,
            relationship_to_student="parent",
        )
        today = timezone.localdate()
        sibling_program = Program.objects.create(
            organization=self.alpha,
            name="Sibling Program",
            active=True,
            start_date=today - datetime.timedelta(days=1),
            end_date=today + datetime.timedelta(days=1),
        )
        Enrollment.objects.create(
            organization=self.alpha,
            student=sibling,
            program=sibling_program,
            active=True,
        )
        Fee.objects.create(
            organization=self.alpha,
            program=sibling_program,
            name="Sibling dues",
            amount=Decimal("40.00"),
            effective_date=today,
        )
        Fee.objects.create(
            organization=self.beta,
            program=self.program,
            name="Foreign-owned dues",
            amount=Decimal("50.00"),
            effective_date=today,
        )

        payments = self._payments()
        self.assertTrue(payments.context["family_balance_unavailable"])
        self.assertIsNone(payments.context["grand_total"])
        payment_rows = {
            row["student"].pk: row for row in payments.context["student_rows"]
        }
        self.assertTrue(payment_rows[self.student.pk]["balance_unavailable"])
        self.assertEqual(
            payment_rows[sibling.pk]["student_total_owed"], Decimal("40.00")
        )
        self.assertContains(payments, "Family balance unavailable")

        dashboard = self._dashboard()
        self.assertTrue(dashboard.context["family_balance_unavailable"])
        self.assertIsNone(dashboard.context["family_total_owed"])
        dashboard_rows = {
            entry["student"].pk: entry for entry in dashboard.context["parent_data"]
        }
        self.assertTrue(
            dashboard_rows[self.student.pk]["active_rows"][0]["balance_unavailable"]
        )
        self.assertEqual(
            dashboard_rows[sibling.pk]["active_rows"][0]["balance"], Decimal("40.00")
        )
        self.assertContains(dashboard, "Family balance unavailable")

    def test_foreign_owned_enrollment_is_not_counted_as_a_local_balance(self):
        Enrollment.objects.filter(pk=self.enrollment.pk).update(organization=self.beta)

        payments = self._payments()
        self.assertNotContains(payments, "Local Program")
        self.assertNotContains(payments, "Total Due: $")
        self.assertNotContains(payments, "Paid in Full")

        dashboard = self._dashboard()
        self.assertNotContains(dashboard, "Local Program")
        self.assertNotContains(dashboard, "$75.00")
        self.assertNotContains(dashboard, "Paid in full")

    def test_parent_balance_views_reject_missing_organization_context(self):
        request = RequestFactory().get(reverse("parent_payments"))
        request.user = self.parent_user
        request.organization = None

        with self.assertRaises(Http404):
            ParentPaymentsView.as_view()(request)

        request = RequestFactory().get(reverse("profile_dashboard"))
        request.user = self.parent_user
        request.organization = None

        with self.assertRaises(Http404):
            DashboardView.as_view()(request)
