import shutil
import tempfile
from decimal import Decimal

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models.signals import post_init
from django.http import Http404
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from GoSAdminPortal.test_runner import _assign_default_test_org
from organizations.models import (
    Organization,
    OrganizationSettings,
    UserOrganizationMembership,
)
from programs.models import (
    Adult,
    AdultStudentRelationship,
    Enrollment,
    Fee,
    Payment,
    Program,
    SlidingScale,
    Student,
    TaxForm,
)
from programs.permission_views import can_user_read, user_is_parent
from programs.views.finances import (
    SlidingScaleApplyView,
    SlidingScaleReviewListView,
    SlidingScaleWithdrawView,
)

_MEDIA_ROOT = tempfile.mkdtemp(prefix="sliding-scale-isolation-")
_FILE_ENCRYPTION_KEY = Fernet.generate_key().decode()


@override_settings(
    ALLOWED_HOSTS=[".localhost", "testserver"],
    FILE_ENCRYPTION_KEY=_FILE_ENCRYPTION_KEY,
    MEDIA_ROOT=_MEDIA_ROOT,
    MOMENTUM_ROOT_DOMAIN="localhost",
)
class SlidingScaleRelatedRecordIsolationTests(TestCase):
    tenant_models = (
        Adult,
        AdultStudentRelationship,
        Enrollment,
        Fee,
        Payment,
        Program,
        SlidingScale,
        Student,
        TaxForm,
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

        self.alpha = Organization.objects.create(
            name="Sliding Alpha", slug="sliding-alpha", subdomain="sliding-alpha"
        )
        self.beta = Organization.objects.create(
            name="Sliding Beta", slug="sliding-beta", subdomain="sliding-beta"
        )
        self.alpha_host = "sliding-alpha.localhost"
        self.program = Program.objects.create(
            organization=self.alpha, name="Alpha Program", active=True
        )
        self.student = Student.objects.create(
            organization=self.alpha,
            legal_first_name="Avery",
            last_name="Alpha",
            personal_email="avery@example.test",
        )
        self.parent = Adult.objects.create(
            organization=self.alpha,
            legal_first_name="Pat",
            last_name="Parent",
            personal_email="parent@example.test",
            email_updates=True,
            is_parent=True,
            login_enabled=True,
        )
        AdultStudentRelationship.objects.create(
            organization=self.alpha,
            adult=self.parent,
            student=self.student,
            relationship_to_student="parent",
        )
        self.student.primary_contact = self.parent
        self.student.save()
        self.parent_user = get_user_model().objects.create_user(
            username="sliding-parent", password="password"
        )  # nosec B106
        self.parent.user = self.parent_user
        self.parent.save()
        UserOrganizationMembership.objects.update_or_create(
            user=self.parent_user,
            organization=self.alpha,
            role="Parent",
            defaults={"is_active": True},
        )
        self.lead_user = get_user_model().objects.create_user(
            username="sliding-lead",
            email="alpha-lead@example.test",
            password="password",
        )  # nosec B106
        UserOrganizationMembership.objects.update_or_create(
            user=self.lead_user,
            organization=self.alpha,
            role="LeadMentor",
            defaults={"is_active": True},
        )
        Enrollment.objects.create(
            organization=self.alpha,
            student=self.student,
            program=self.program,
            active=True,
        )

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_MEDIA_ROOT, ignore_errors=True)

    def _restore_default_organization_hook(self):
        for model in self.disconnected_models:
            post_init.connect(
                receiver=_assign_default_test_org,
                sender=model,
                dispatch_uid=f"default-test-org-{model._meta.label}",
            )

    def _tax_form(self, organization, sliding_scale, filename):
        return TaxForm.objects.create(
            organization=organization,
            sliding_scale=sliding_scale,
            file=SimpleUploadedFile(filename, b"confidential tax document"),
        )

    def test_parent_application_explicitly_owns_scale_and_tax_form(self):
        self.client.force_login(self.parent_user)
        response = self.client.post(
            reverse("sliding_scale_apply", args=[self.student.pk]),
            {
                "family_size": 4,
                "adjusted_gross_income": "30000.00",
                "documents": SimpleUploadedFile("income.pdf", b"income data"),
            },
            HTTP_HOST=self.alpha_host,
        )

        self.assertEqual(response.status_code, 302)
        application = SlidingScale.objects.get(student=self.student)
        tax_form = TaxForm.objects.get(sliding_scale=application)
        self.assertEqual(application.organization_id, self.alpha.pk)
        self.assertEqual(tax_form.organization_id, self.alpha.pk)

    def test_parent_cannot_apply_for_foreign_student_through_mismatched_link(self):
        foreign_student = Student.objects.create(
            organization=self.beta,
            legal_first_name="Private",
            last_name="Beta Student",
        )
        AdultStudentRelationship.objects.create(
            organization=self.alpha,
            adult=self.parent,
            student=foreign_student,
            relationship_to_student="parent",
        )
        self.client.force_login(self.parent_user)

        request = RequestFactory().get("/")
        request.organization = self.alpha
        request.user = self.parent_user
        with self.assertRaises(Http404):
            SlidingScaleApplyView()._get_student_for_parent(request, foreign_student.pk)
        self.assertFalse(SlidingScale.objects.filter(student=foreign_student).exists())

    def test_parent_cannot_withdraw_foreign_scale_linked_to_local_student(self):
        scale = SlidingScale.objects.create(
            organization=self.beta,
            student=self.student,
            status=SlidingScale.STATUS_PENDING,
            family_size=3,
            adjusted_gross_income=Decimal("25000.00"),
        )
        tax_form = self._tax_form(self.beta, scale, "foreign-income.pdf")
        tax_form_path = tax_form.file.name
        self.assertTrue(user_is_parent(self.parent_user, org=self.alpha))
        self.assertTrue(
            can_user_read(
                self.parent_user, "student_info", self.student, org=self.alpha
            )
        )

        request = RequestFactory().post("/")
        request.organization = self.alpha
        request.user = self.parent_user
        with self.assertRaises(Http404):
            SlidingScaleWithdrawView().post(request, pk=scale.pk)
        self.assertTrue(SlidingScale.objects.filter(pk=scale.pk).exists())
        self.assertTrue(TaxForm.objects.filter(pk=tax_form.pk).exists())
        self.assertTrue(tax_form.file.storage.exists(tax_form_path))

    def test_withdrawal_refuses_to_cascade_into_foreign_tax_form(self):
        scale = SlidingScale.objects.create(
            organization=self.alpha,
            student=self.student,
            status=SlidingScale.STATUS_PENDING,
            family_size=3,
            adjusted_gross_income=Decimal("25000.00"),
        )
        tax_form = self._tax_form(self.beta, scale, "foreign-linked-income.pdf")
        tax_form_path = tax_form.file.name
        request = RequestFactory().post("/")
        request.organization = self.alpha
        request.user = self.parent_user
        request.session = {}
        request._messages = FallbackStorage(request)
        response = SlidingScaleWithdrawView().post(request, pk=scale.pk)

        self.assertEqual(response.status_code, 302)
        self.assertTrue(SlidingScale.objects.filter(pk=scale.pk).exists())
        self.assertTrue(TaxForm.objects.filter(pk=tax_form.pk).exists())
        self.assertTrue(tax_form.file.storage.exists(tax_form_path))

    def test_review_list_hides_local_scale_linked_to_foreign_student(self):
        foreign_student = Student.objects.create(
            organization=self.beta,
            legal_first_name="Confidential",
            last_name="Beta Student",
        )
        scale = SlidingScale.objects.create(
            organization=self.alpha,
            student=foreign_student,
            status=SlidingScale.STATUS_PENDING,
        )
        request = RequestFactory().get("/")
        request.organization = self.alpha
        request.user = self.lead_user
        view = SlidingScaleReviewListView()
        view.request = request
        self.assertFalse(view.get_queryset().filter(pk=scale.pk).exists())

    def test_sliding_scale_notification_omits_foreign_enrolled_program(self):
        foreign_program = Program.objects.create(
            organization=self.beta, name="Beta Confidential Program"
        )
        Enrollment.objects.create(
            organization=self.beta,
            student=self.student,
            program=foreign_program,
            active=True,
        )
        mail.outbox = []

        SlidingScale.objects.create(
            organization=self.alpha,
            student=self.student,
            status=SlidingScale.STATUS_PENDING,
        )

        contents = "\n".join(
            message.subject
            + "\n"
            + message.body
            + "\n"
            + "\n".join(part[0] for part in message.alternatives)
            for message in mail.outbox
        )
        self.assertNotIn("Beta Confidential Program", contents)

    def test_sliding_scale_reviewer_notices_are_generic_and_local(self):
        User = get_user_model()
        another_local_lead = User.objects.create_user(
            username="another-alpha-lead",
            email="second-alpha-lead@example.test",
            password="password",
        )  # nosec B106
        UserOrganizationMembership.objects.create(
            user=another_local_lead,
            organization=self.alpha,
            role="LeadMentor",
            is_active=True,
        )
        duplicate_address_lead = User.objects.create_user(
            username="duplicate-alpha-lead",
            email="ALPHA-LEAD@example.test",
            password="password",
        )  # nosec B106
        UserOrganizationMembership.objects.create(
            user=duplicate_address_lead,
            organization=self.alpha,
            role="LeadMentor",
            is_active=True,
        )
        foreign_lead = User.objects.create_user(
            username="beta-lead",
            email="beta-lead@example.test",
            password="password",
        )  # nosec B106
        UserOrganizationMembership.objects.create(
            user=foreign_lead,
            organization=self.beta,
            role="LeadMentor",
            is_active=True,
        )
        inactive_member = User.objects.create_user(
            username="inactive-alpha-lead",
            email="inactive-member@example.test",
            password="password",
        )  # nosec B106
        UserOrganizationMembership.objects.create(
            user=inactive_member,
            organization=self.alpha,
            role="LeadMentor",
            is_active=False,
        )
        inactive_user = User.objects.create_user(
            username="inactive-alpha-user",
            email="inactive-user@example.test",
            password="password",
            is_active=False,
        )  # nosec B106
        UserOrganizationMembership.objects.create(
            user=inactive_user,
            organization=self.alpha,
            role="LeadMentor",
            is_active=True,
        )
        not_a_lead = User.objects.create_user(
            username="alpha-mentor",
            email="mentor@example.test",
            password="password",
        )  # nosec B106
        UserOrganizationMembership.objects.create(
            user=not_a_lead,
            organization=self.alpha,
            role="Mentor",
            is_active=True,
        )
        no_email_lead = User.objects.create_user(
            username="alpha-lead-without-email", password="password"
        )  # nosec B106
        UserOrganizationMembership.objects.create(
            user=no_email_lead,
            organization=self.alpha,
            role="LeadMentor",
            is_active=True,
        )
        invalid_email_lead = User.objects.create_user(
            username="alpha-lead-with-invalid-email",
            email="not-an-email",
            password="password",
        )  # nosec B106
        UserOrganizationMembership.objects.create(
            user=invalid_email_lead,
            organization=self.alpha,
            role="LeadMentor",
            is_active=True,
        )
        mail.outbox = []

        SlidingScale.objects.create(
            organization=self.alpha,
            student=self.student,
            status=SlidingScale.STATUS_PENDING,
            family_size=5,
            adjusted_gross_income=Decimal("12345.67"),
        )

        reviewer_messages = [
            message
            for message in mail.outbox
            if "Sliding Scale Application to Review" in message.subject
        ]
        self.assertEqual(
            {tuple(message.to) for message in reviewer_messages},
            {
                ("alpha-lead@example.test",),
                ("second-alpha-lead@example.test",),
            },
        )
        for message in reviewer_messages:
            content = "\n".join(
                [message.subject, message.body]
                + [part[0] for part in message.alternatives]
            )
            self.assertEqual(message.subject, "New Sliding Scale Application to Review")
            self.assertNotIn("Avery Alpha", content)
            self.assertNotIn("Alpha Program", content)
            self.assertNotIn("12345.67", content)
            self.assertNotIn("Household Size", content)

    @override_settings(LEAD_MENTOR_NOTIFICATION_EMAIL="global-lead@example.test")
    def test_sliding_scale_notice_does_not_fall_back_to_global_address(self):
        UserOrganizationMembership.objects.filter(
            user=self.lead_user,
            organization=self.alpha,
            role="LeadMentor",
        ).update(is_active=False)
        mail.outbox = []

        SlidingScale.objects.create(
            organization=self.alpha,
            student=self.student,
            status=SlidingScale.STATUS_PENDING,
        )

        self.assertNotIn(
            "global-lead@example.test",
            [address for message in mail.outbox for address in message.to],
        )

    def test_fee_and_payment_emails_use_only_local_payment_guidance(self):
        OrganizationSettings.objects.create(
            organization=self.alpha,
            data={
                "payment_settings": {
                    "instructions": "Send <script>alert('x')</script> by mail.\nUse bank transfer & include the student name.",
                    "online_payment_url": "https://pay.alpha.example.test/checkout?a=1&b=2",
                }
            },
        )
        OrganizationSettings.objects.create(
            organization=self.beta,
            data={
                "payment_settings": {
                    "instructions": "Beta confidential payment guidance",
                    "online_payment_url": "https://pay.beta.example.test/",
                }
            },
        )
        mail.outbox = []

        Fee.objects.create(
            organization=self.alpha,
            program=self.program,
            name="Alpha Registration",
            amount=Decimal("40.00"),
        )
        Payment.objects.create(
            organization=self.alpha,
            student=self.student,
            program=self.program,
            amount=Decimal("10.00"),
            paid_on=self.student.created_at.date(),
            paid_via="cash",
        )

        fee_email = next(
            message for message in mail.outbox if message.subject.startswith("New Fee")
        )
        payment_email = next(
            message
            for message in mail.outbox
            if message.subject.startswith("Payment Recorded")
        )
        for message in (fee_email, payment_email):
            html_content = message.alternatives[0][0]
            content = message.body + "\n" + html_content
            self.assertIn("Send <script>alert('x')</script> by mail.", message.body)
            self.assertIn("&lt;script&gt;", html_content)
            self.assertNotIn("<script>", html_content)
            self.assertIn("https://pay.alpha.example.test/checkout", content)
            self.assertNotIn("pay.beta.example.test", content)
            self.assertNotIn("Beta confidential payment guidance", content)
            self.assertNotIn("commerce.cashnet.com", content)
            self.assertNotIn("Carnegie Mellon University", content)
            self.assertNotIn("Lynnetta Miller", content)
            self.assertNotIn("Girls of Steel Robotics", content)
            self.assertIn("Momentum", html_content)

        self.assertIn("Alpha Registration", fee_email.body)
        self.assertIn("Avery Alpha", payment_email.body)

    def test_fee_email_uses_neutral_payment_fallback_for_blank_guidance(self):
        mail.outbox = []

        Fee.objects.create(
            organization=self.alpha,
            program=self.program,
            name="Alpha Registration",
            amount=Decimal("40.00"),
        )
        Payment.objects.create(
            organization=self.alpha,
            student=self.student,
            program=self.program,
            amount=Decimal("10.00"),
            paid_on=self.student.created_at.date(),
            paid_via="cash",
        )

        messages_by_subject = {message.subject: message for message in mail.outbox}
        for prefix in ("New Fee", "Payment Recorded"):
            message = next(
                message
                for subject, message in messages_by_subject.items()
                if subject.startswith(prefix)
            )
            contents = message.body + "\n" + message.alternatives[0][0]
            self.assertIn(
                "Please contact the lead mentors to learn more about how to pay these dues.",
                contents,
            )
            self.assertNotIn("commerce.cashnet.com", contents)
            self.assertNotIn("Carnegie Mellon University", contents)
            self.assertNotIn("Girls of Steel Robotics", contents)

    def test_review_cleanup_preserves_foreign_form_and_deletes_local_form(self):
        scale = SlidingScale.objects.create(
            organization=self.alpha,
            student=self.student,
            status=SlidingScale.STATUS_PENDING,
        )
        local_form = self._tax_form(self.alpha, scale, "local-income.pdf")
        foreign_form = self._tax_form(self.beta, scale, "foreign-income.pdf")
        local_path = local_form.file.name
        foreign_path = foreign_form.file.name
        scale.status = SlidingScale.STATUS_APPROVED
        scale.save()

        self.assertFalse(TaxForm.objects.filter(pk=local_form.pk).exists())
        self.assertFalse(local_form.file.storage.exists(local_path))
        self.assertTrue(TaxForm.objects.filter(pk=foreign_form.pk).exists())
        self.assertTrue(foreign_form.file.storage.exists(foreign_path))
