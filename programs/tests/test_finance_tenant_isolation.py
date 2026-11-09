import tempfile
from datetime import date
from decimal import Decimal

from cryptography.fernet import Fernet
from django.contrib.auth.models import Permission, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import Http404
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from organizations.models import Organization, UserOrganizationMembership
from programs.forms import FeeAssignmentEditForm, FeeForm
from programs.models import (
    Enrollment,
    Fee,
    FeeAssignment,
    Payment,
    Program,
    SlidingScale,
    Student,
    TaxForm,
)
from programs.views.finances import (
    ProgramDuesOwedView,
    SlidingScaleTaxFormDeleteView,
    SlidingScaleTaxFormViewView,
)


@override_settings(
    ALLOWED_HOSTS=["*"],
    MOMENTUM_ROOT_DOMAIN="localhost",
    FILE_ENCRYPTION_KEY=Fernet.generate_key().decode(),
)
class FinanceTenantIsolationTests(TestCase):
    def setUp(self):
        self.media_dir = tempfile.TemporaryDirectory()
        media_settings = override_settings(MEDIA_ROOT=self.media_dir.name)
        media_settings.enable()
        self.addCleanup(media_settings.disable)
        self.addCleanup(self.media_dir.cleanup)

        self.alpha = Organization.objects.create(
            name="Finance Alpha", slug="finance-alpha", subdomain="finance-alpha"
        )
        self.beta = Organization.objects.create(
            name="Finance Beta", slug="finance-beta", subdomain="finance-beta"
        )
        self.alpha_host = "finance-alpha.localhost"
        self.beta_host = "finance-beta.localhost"

        self.user = User.objects.create_user(username="finance-lead")
        UserOrganizationMembership.objects.create(
            organization=self.alpha,
            user=self.user,
            role="LeadMentor",
            is_active=True,
        )
        fee_permissions = Permission.objects.filter(
            content_type__app_label="programs",
            codename__in=("add_fee", "change_fee"),
        )
        self.user.user_permissions.add(*fee_permissions)
        self.client.force_login(self.user)

        self.program = Program.objects.create(
            organization=self.alpha, name="Alpha Program"
        )
        self.foreign_program = Program.objects.create(
            organization=self.beta, name="Beta Secret Program"
        )
        self.student = Student.objects.create(
            organization=self.alpha,
            legal_first_name="Alpha",
            last_name="Student",
        )
        self.foreign_student = Student.objects.create(
            organization=self.beta,
            legal_first_name="BetaSecret",
            last_name="Student",
        )
        Enrollment.objects.create(
            organization=self.alpha, program=self.program, student=self.student
        )
        Enrollment.objects.create(
            organization=self.beta,
            program=self.foreign_program,
            student=self.foreign_student,
        )
        self.fee = Fee.objects.create(
            organization=self.alpha,
            program=self.program,
            name="Alpha Tuition",
            amount=Decimal("125.00"),
        )
        self.foreign_fee = Fee.objects.create(
            organization=self.beta,
            program=self.foreign_program,
            name="Beta Secret Tuition",
            amount=Decimal("700.00"),
        )
        FeeAssignment.objects.create(
            organization=self.alpha, fee=self.fee, student=self.student
        )
        FeeAssignment.objects.create(
            organization=self.beta,
            fee=self.foreign_fee,
            student=self.foreign_student,
        )
        self.alpha_scale = SlidingScale.objects.create(
            organization=self.alpha,
            student=self.student,
            status=SlidingScale.STATUS_PENDING,
        )
        self.beta_scale = SlidingScale.objects.create(
            organization=self.beta,
            student=self.foreign_student,
            status=SlidingScale.STATUS_PENDING,
        )
        self.alpha_tax_form = self._create_tax_form(
            self.alpha, self.alpha_scale, b"alpha tax document"
        )
        self.beta_tax_form = self._create_tax_form(
            self.beta, self.beta_scale, b"beta secret tax document"
        )

    @staticmethod
    def _create_tax_form(organization, sliding_scale, content):
        return TaxForm.objects.create(
            organization=organization,
            sliding_scale=sliding_scale,
            file=SimpleUploadedFile(
                "income.pdf", content, content_type="application/pdf"
            ),
        )

    def tenant_get(self, url):
        return self.client.get(url, HTTP_HOST=self.alpha_host)

    def test_foreign_program_fee_assignment_page_is_not_accessible(self):
        paths = (
            reverse("program_fee_select", args=[self.foreign_program.pk]),
            reverse("program_fee_create", args=[self.foreign_program.pk]),
            reverse(
                "program_fee_edit",
                args=[self.foreign_program.pk, self.foreign_fee.pk],
            ),
            reverse(
                "program_fee_assignments",
                args=[self.foreign_program.pk, self.foreign_fee.pk],
            ),
        )

        for path in paths:
            with self.subTest(path=path):
                response = self.tenant_get(path)
                self.assertNotEqual(response.status_code, 200)
                self.assertNotIn(b"BetaSecret", response.content)
                self.assertNotIn(b"Beta Secret Program", response.content)

    def test_fee_assignment_choices_and_submissions_exclude_foreign_students(self):
        Enrollment.objects.create(
            organization=self.alpha,
            program=self.program,
            student=self.foreign_student,
            active=True,
        )
        form = FeeAssignmentEditForm(
            program=self.program,
            fee=self.fee,
            data={"students": [str(self.foreign_student.pk)]},
        )

        self.assertNotIn(
            self.foreign_student,
            list(
                FeeAssignmentEditForm(program=self.program, fee=self.fee)
                .fields["students"]
                .queryset
            ),
        )
        self.assertFalse(form.is_valid())
        self.assertFalse(
            FeeAssignment.objects.filter(
                fee=self.fee, student=self.foreign_student
            ).exists()
        )

    def test_fee_form_rejects_a_tampered_foreign_program(self):
        form = FeeForm(
            data={
                "program": str(self.foreign_program.pk),
                "name": "Tampered fee",
                "amount": "50.00",
                "effective_date": "",
                "due_date": "",
                "students": [],
            },
            program=self.program,
        )

        self.assertFalse(form.is_valid())

    def test_fee_assignment_form_does_not_mutate_foreign_owned_rows(self):
        FeeAssignment.objects.filter(fee=self.fee, student=self.student).update(
            organization=self.beta
        )
        form = FeeAssignmentEditForm(
            data={"students": [str(self.student.pk)]},
            program=self.program,
            fee=self.fee,
        )

        self.assertFalse(form.is_valid())
        assignment = FeeAssignment.objects.get(fee=self.fee, student=self.student)
        self.assertEqual(assignment.organization_id, self.beta.pk)

    def test_fee_form_rejects_mismatched_existing_assignments(self):
        FeeAssignment.objects.filter(fee=self.fee, student=self.student).update(
            organization=self.beta
        )
        form = FeeForm(
            data={
                "program": str(self.program.pk),
                "name": self.fee.name,
                "amount": str(self.fee.amount),
                "effective_date": "",
                "due_date": "",
                "students": [str(self.student.pk)],
            },
            instance=self.fee,
            program=self.program,
        )

        self.assertFalse(form.is_valid())

    def test_foreign_balance_and_dues_views_do_not_expose_program_data(self):
        paths = (
            reverse(
                "program_student_balance",
                args=[self.foreign_program.pk, self.foreign_student.pk],
            ),
            reverse(
                "program_student_balance_print",
                args=[self.foreign_program.pk, self.foreign_student.pk],
            ),
            reverse("program_dues_owed", args=[self.foreign_program.pk]),
        )

        for path in paths:
            with self.subTest(path=path):
                response = self.tenant_get(path)
                self.assertNotIn(b"BetaSecret", response.content)
                self.assertNotIn(b"Beta Secret Program", response.content)
                self.assertNotIn(b"Beta Secret Tuition", response.content)

    def test_inconsistent_foreign_fee_does_not_appear_in_local_balance_views(self):
        Fee.objects.create(
            organization=self.beta,
            program=self.program,
            name="Foreign Owner Secret Fee",
            amount=Decimal("999.00"),
        )
        paths = (
            reverse("program_student_balance", args=[self.program.pk, self.student.pk]),
            reverse(
                "program_student_balance_print", args=[self.program.pk, self.student.pk]
            ),
            reverse("program_dues_owed", args=[self.program.pk]),
        )

        for path in paths:
            with self.subTest(path=path):
                response = self.tenant_get(path)
                self.assertNotIn(b"Foreign Owner Secret Fee", response.content)
                self.assertNotIn(b"$999.00", response.content)

    def test_foreign_owned_enrollment_withholds_dues_total_without_exposing_student(
        self,
    ):
        mismatched_student = Student.objects.create(
            organization=self.alpha,
            legal_first_name="Mismatched",
            last_name="Enrollment",
        )
        Enrollment.objects.create(
            organization=self.beta,
            program=self.program,
            student=mismatched_student,
        )

        response = self.tenant_get(reverse("program_dues_owed", args=[self.program.pk]))

        self.assertIn(b"Unavailable pending review", response.content)
        self.assertNotIn(b"Mismatched Enrollment", response.content)

    def test_foreign_tax_form_cannot_be_viewed(self):
        response = self.tenant_get(
            reverse(
                "sliding_scale_tax_form_view",
                args=[self.beta_scale.pk, self.beta_tax_form.pk],
            )
        )

        self.assertNotEqual(response.status_code, 200)
        if getattr(response, "streaming", False):
            self.assertNotIn(
                b"beta secret tax document", b"".join(response.streaming_content)
            )

    def test_foreign_tax_form_cannot_be_deleted(self):
        response = self.client.post(
            reverse(
                "sliding_scale_delete_tax_form",
                args=[self.beta_scale.pk, self.beta_tax_form.pk],
            ),
            HTTP_HOST=self.alpha_host,
        )

        self.assertTrue(TaxForm.objects.filter(pk=self.beta_tax_form.pk).exists())
        self.assertTrue(
            self.beta_tax_form.file.storage.exists(self.beta_tax_form.file.name)
        )
        self.assertNotEqual(response.status_code, 200)

    def test_global_permission_does_not_authorize_foreign_tax_form_view(self):
        self._grant_beta_student_role_and_sliding_scale_permission()

        response = self.client.get(
            reverse(
                "sliding_scale_tax_form_view",
                args=[self.beta_scale.pk, self.beta_tax_form.pk],
            ),
            HTTP_HOST=self.beta_host,
        )

        self.assertNotEqual(response.status_code, 200)

    def test_global_permission_does_not_authorize_foreign_tax_form_deletion(self):
        self._grant_beta_student_role_and_sliding_scale_permission()

        response = self.client.post(
            reverse(
                "sliding_scale_delete_tax_form",
                args=[self.beta_scale.pk, self.beta_tax_form.pk],
            ),
            HTTP_HOST=self.beta_host,
        )

        self.assertNotEqual(response.status_code, 200)
        self.assertTrue(TaxForm.objects.filter(pk=self.beta_tax_form.pk).exists())
        self.assertTrue(
            self.beta_tax_form.file.storage.exists(self.beta_tax_form.file.name)
        )

    def _grant_beta_student_role_and_sliding_scale_permission(self):
        UserOrganizationMembership.objects.create(
            organization=self.beta,
            user=self.user,
            role="Student",
            is_active=True,
        )
        permission = Permission.objects.get(
            content_type__app_label="programs",
            codename="change_slidingscale",
        )
        self.user.user_permissions.add(permission)

    def test_tax_form_permissions_fail_closed_without_organization(self):
        request = RequestFactory().get("/")
        request.user = User.objects.create_superuser(username="tax-form-admin")
        request.organization = None

        for view_class in (
            SlidingScaleTaxFormViewView,
            SlidingScaleTaxFormDeleteView,
        ):
            with self.subTest(view=view_class.__name__):
                view = view_class()
                view.request = request
                self.assertFalse(view.test_func())

        request.organization = self.alpha
        for view_class in (
            SlidingScaleTaxFormViewView,
            SlidingScaleTaxFormDeleteView,
        ):
            with self.subTest(view=view_class.__name__, organization="alpha"):
                view = view_class()
                view.request = request
                self.assertTrue(view.test_func())

    def test_tax_form_with_foreign_sliding_scale_is_not_accessible(self):
        mismatched_form = self._create_tax_form(
            self.alpha, self.beta_scale, b"mismatched tax document"
        )
        view_response = self.tenant_get(
            reverse(
                "sliding_scale_tax_form_view",
                args=[self.beta_scale.pk, mismatched_form.pk],
            )
        )
        delete_response = self.client.post(
            reverse(
                "sliding_scale_delete_tax_form",
                args=[self.beta_scale.pk, mismatched_form.pk],
            ),
            HTTP_HOST=self.alpha_host,
        )

        self.assertNotEqual(view_response.status_code, 200)
        self.assertTrue(TaxForm.objects.filter(pk=mismatched_form.pk).exists())
        self.assertNotEqual(delete_response.status_code, 200)

    def test_same_tenant_financial_pages_and_tax_form_remain_available(self):
        dues_response = self.tenant_get(
            reverse("program_dues_owed", args=[self.program.pk])
        )
        balance_response = self.tenant_get(
            reverse(
                "program_student_balance_print", args=[self.program.pk, self.student.pk]
            )
        )
        tax_response = self.tenant_get(
            reverse(
                "sliding_scale_tax_form_view",
                args=[self.alpha_scale.pk, self.alpha_tax_form.pk],
            )
        )

        self.assertEqual(dues_response.status_code, 200)
        self.assertIn(b"Alpha", dues_response.content)
        assignments_response = self.tenant_get(
            reverse("program_fee_assignments", args=[self.program.pk, self.fee.pk])
        )
        self.assertEqual(assignments_response.status_code, 200)
        self.assertIn(b"Alpha Student", assignments_response.content)
        self.assertEqual(balance_response.status_code, 200)
        self.assertIn(b"Alpha", balance_response.content)
        self.assertEqual(tax_response.status_code, 200)
        self.assertEqual(
            b"".join(tax_response.streaming_content), b"alpha tax document"
        )

    def test_dues_view_fails_closed_without_organization_context(self):
        request = RequestFactory().get("/")
        request.user = User.objects.create_superuser(username="platform-admin")
        request.organization = None

        with self.assertRaises(Http404):
            ProgramDuesOwedView.as_view()(request, pk=self.program.pk)

    def test_membership_only_lead_can_open_local_fee_and_payment_forms(self):
        self.user.user_permissions.clear()
        urls = (
            reverse("program_fee_create", args=[self.program.pk]),
            reverse("program_fee_edit", args=[self.program.pk, self.fee.pk]),
            reverse("program_fee_assignments", args=[self.program.pk, self.fee.pk]),
            reverse("program_payment_create", args=[self.program.pk]),
        )

        for url in urls:
            with self.subTest(url=url):
                response = self.tenant_get(url)
                self.assertEqual(response.status_code, 200)

    def test_membership_only_lead_cannot_delete_foreign_payment(self):
        self.user.user_permissions.clear()
        foreign_payment = Payment.objects.create(
            organization=self.beta,
            program=self.foreign_program,
            student=self.foreign_student,
            amount=Decimal("20.00"),
            paid_on=date(2026, 1, 1),
        )

        response = self.client.post(
            reverse(
                "program_payment_delete",
                args=[self.program.pk, foreign_payment.pk],
            ),
            HTTP_HOST=self.alpha_host,
        )

        self.assertNotEqual(response.status_code, 200)
        self.assertTrue(Payment.objects.filter(pk=foreign_payment.pk).exists())
