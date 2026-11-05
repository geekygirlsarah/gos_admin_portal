from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core import mail
from django.http import Http404
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

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
    FeeAssignment,
    Payment,
    Program,
    SlidingScale,
    Student,
)
from programs.views.finances import ProgramEmailBalancesView


@override_settings(
    MAILERS={"default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}},
    EMAIL_SENDER_ACCOUNTS=[
        {"key": "platform-named", "email": "platform-named@example.test"}
    ],
    DEFAULT_FROM_EMAIL="platform@example.test",
    NOTIFICATION_FROM_EMAIL=None,
    TESTING=True,
)
class BalanceEmailTenantIsolationTests(TestCase):
    def setUp(self):
        self.gos = Organization.objects.get(slug="gos")
        self.alpha = Organization.objects.create(
            name="Balance Alpha", slug="balance-alpha", subdomain="balance-alpha"
        )
        self.beta = Organization.objects.create(
            name="Balance Beta", slug="balance-beta", subdomain="balance-beta"
        )
        self.host = "balance-alpha.localhost"
        self.beta_host = "balance-beta.localhost"
        self.user = User.objects.create_user(username="balance-lead")
        for org in (self.gos, self.alpha, self.beta):
            UserOrganizationMembership.objects.create(
                organization=org, user=self.user, role="LeadMentor", is_active=True
            )
        self.client.force_login(self.user)

        for org, key, brand in (
            (self.gos, "gos-sender", "GoS Secret Brand"),
            (self.alpha, "alpha-sender", "Alpha Statement Brand"),
            (self.beta, "beta-sender", "Beta Statement Brand"),
        ):
            OrganizationSettings.objects.update_or_create(
                organization=org,
                defaults={
                    "data": {
                        "email_settings": {
                            "organization_name": brand,
                            "notification_from_email": f"{key}-default@example.test",
                            "reply_to_email": f"{key}-reply@example.test",
                            "sender_accounts": [
                                {
                                    "key": key,
                                    "email": f"{key}@example.test",
                                    "display_name": key,
                                }
                            ],
                        }
                    }
                },
            )

        self.program = Program.objects.create(
            organization=self.alpha, name="Alpha Program", active=True
        )
        self.foreign_program = Program.objects.create(
            organization=self.beta, name="Beta Secret Program", active=True
        )
        self.student = self.make_student(self.alpha, "Alpha")
        self.foreign_student = self.make_student(self.beta, "BetaSecret")
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
            name="Local tuition",
            amount=Decimal("125.00"),
        )
        self.parent = self.make_parent(self.alpha, "alpha-parent@example.test")
        self.foreign_parent = self.make_parent(self.beta, "beta-parent@example.test")
        AdultStudentRelationship.objects.create(
            organization=self.alpha, student=self.student, adult=self.parent
        )
        AdultStudentRelationship.objects.create(
            organization=self.beta,
            student=self.foreign_student,
            adult=self.foreign_parent,
        )
        mail.outbox = []

    def make_student(self, organization, name):
        return Student.objects.create(
            organization=organization, legal_first_name=name, last_name="Student"
        )

    def make_parent(self, organization, email):
        return Adult.objects.create(
            organization=organization,
            legal_first_name="Guardian",
            last_name="Contact",
            personal_email=email,
            is_parent=True,
            email_updates=True,
            login_enabled=True,
        )

    def url(self, program=None):
        return reverse("program_dues_email", args=[(program or self.program).pk])

    def post_statement(self, program=None, host=None, **extra):
        program = program or self.program
        data = {
            "program": extra.pop("hidden_program", str(program.pk)),
            "subject": "Balance statement",
            "recipient_filter": "all",
            "from_account": "DEFAULT",
            **extra,
        }
        return self.client.post(self.url(program), data, HTTP_HOST=host or self.host)

    def test_local_balance_label_is_preserved(self):
        response = self.client.get(self.url(), HTTP_HOST=self.host)
        self.assertContains(response, "Alpha Student ($125.00)")

    def test_foreign_program_url_does_not_disclose_or_send(self):
        with self.subTest(method="GET"):
            response = self.client.get(
                self.url(self.foreign_program), HTTP_HOST=self.host
            )
            self.assertNotIn(b"Beta Secret Program", response.content)
            self.assertNotIn(b"BetaSecret Student", response.content)
        with self.subTest(method="POST"):
            self.post_statement(self.foreign_program)
            self.assertEqual(mail.outbox, [])

    def test_hidden_program_must_match_url(self):
        other_local = Program.objects.create(
            organization=self.alpha, name="Other Alpha Program"
        )
        for program in (self.foreign_program, other_local):
            with self.subTest(program=program.name):
                mail.outbox = []
                self.post_statement(hidden_program=str(program.pk))
                self.assertEqual(mail.outbox, [])

    def test_sender_choices_are_only_current_organization_and_default(self):
        for host, url, expected in (
            (self.host, self.url(), "alpha-sender"),
            (self.beta_host, self.url(self.foreign_program), "beta-sender"),
        ):
            with self.subTest(host=host):
                response = self.client.get(url, HTTP_HOST=host)
                choices = dict(response.context["form"].fields["from_account"].choices)
                self.assertEqual(set(choices), {"DEFAULT", expected})

    def test_unconfigured_tenant_does_not_offer_platform_named_sender(self):
        OrganizationSettings.objects.filter(organization=self.alpha).update(
            data={"email_settings": {}}
        )
        response = self.client.get(self.url(), HTTP_HOST=self.host)
        choices = dict(response.context["form"].fields["from_account"].choices)
        self.assertEqual(set(choices), {"DEFAULT"})

    def test_confirmation_identifies_the_current_organization(self):
        response = self.client.get(self.url(), HTTP_HOST=self.host)

        self.assertContains(
            response, "This message is written on behalf of Balance Alpha."
        )
        self.assertNotContains(
            response, "This message is written on behalf of Girls of Steel Robotics."
        )

    def test_invalid_recipient_warning_does_not_identify_student(self):
        self.parent.personal_email = " "
        self.parent.org_email = ""
        self.parent.save(update_fields=["personal_email", "org_email"])

        with self.assertLogs("programs.email", level="WARNING") as captured:
            self.post_statement()

        self.assertNotIn(self.student.display_name, " ".join(captured.output))
        self.assertEqual(mail.outbox, [])

    def test_dispatch_failure_log_omits_exception_details(self):
        with (
            patch(
                "programs.views.finances.send_messages_in_background",
                side_effect=ValueError("alpha-parent@example.test balance $125"),
            ),
            self.assertLogs("programs.email", level="ERROR") as captured,
        ):
            self.post_statement()

        logs = " ".join(captured.output)
        self.assertIn("ValueError", logs)
        self.assertNotIn("alpha-parent@example.test", logs)
        self.assertNotIn("$125", logs)

    def test_unconfigured_preview_uses_momentum_branding(self):
        OrganizationSettings.objects.filter(organization=self.alpha).update(data={})
        mail.outbox = []

        self.post_statement(test_email="preview@example.test")

        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        html = message.alternatives[0][0]
        self.assertIn("Momentum", html)
        self.assertIn("Momentum", message.from_email)
        self.assertIn("platform@example.test", message.from_email)
        for forbidden in (
            "Girls of Steel",
            "Carnegie Mellon",
            "Elizabeth Kysel",
            "Sarah Withee",
            "girlsofsteelrobotics.org",
        ):
            self.assertNotIn(forbidden, html)

    def test_explicit_balance_branding_is_preserved(self):
        org_settings = OrganizationSettings.objects.get(organization=self.alpha)
        org_settings.data["email_settings"].update(
            address_line1="Alpha Address",
            contact_name="Alpha Contact",
            footer_text="Alpha Footer",
        )
        org_settings.save(update_fields=["data"])
        mail.outbox = []

        self.post_statement(test_email="preview@example.test")

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Alpha Statement Brand", mail.outbox[0].from_email)
        html = mail.outbox[0].alternatives[0][0]
        for value in (
            "Alpha Statement Brand",
            "Alpha Address",
            "Alpha Contact",
            "Alpha Footer",
        ):
            self.assertIn(value, html)

    def test_foreign_sender_submission_is_rejected(self):
        for sender in ("beta-sender", "gos-sender", "platform-named"):
            with self.subTest(sender=sender):
                mail.outbox = []
                self.post_statement(from_account=sender)
                self.assertEqual(mail.outbox, [])

    def test_foreign_student_and_mismatched_enrollments_are_not_selectable(self):
        bad_local = self.make_student(self.alpha, "BadEnrollment")
        Enrollment.objects.create(
            organization=self.beta, program=self.program, student=bad_local
        )
        Enrollment.objects.create(
            organization=self.alpha, program=self.program, student=self.foreign_student
        )
        response = self.client.get(self.url(), HTTP_HOST=self.host)
        choices = set(
            response.context["form"]
            .fields["student"]
            .queryset.values_list("pk", flat=True)
        )
        self.assertEqual(choices, {self.student.pk})

    def test_foreign_student_and_mismatched_enrollment_posts_do_not_send(self):
        bad_local = self.make_student(self.alpha, "BadEnrollment")
        Enrollment.objects.create(
            organization=self.beta, program=self.program, student=bad_local
        )
        Enrollment.objects.create(
            organization=self.alpha, program=self.program, student=self.foreign_student
        )
        bad_parent = self.make_parent(self.alpha, "bad-enrollment@example.test")
        AdultStudentRelationship.objects.create(
            organization=self.alpha, student=bad_local, adult=bad_parent
        )
        mail.outbox = []
        for student in (bad_local, self.foreign_student):
            with self.subTest(student=student.legal_first_name):
                mail.outbox = []
                self.post_statement(
                    recipient_filter="individual", student=str(student.pk)
                )
                self.assertEqual(mail.outbox, [])

    def assert_message_is_local(self, message, recipients, sender):
        self.assertEqual(message.to, recipients)
        self.assertIn(sender, message.from_email)
        html = message.alternatives[0][0]
        self.assertIn("Alpha Statement Brand", html)
        self.assertIn("Alpha Student", html)
        self.assertIn("125.00", html)
        self.assertNotIn("Beta Statement Brand", html)
        self.assertNotIn("GoS Secret Brand", html)
        self.assertNotIn("BetaSecret Student", html)
        self.assertEqual(len(message.bcc), 0)

    def test_actual_statement_uses_local_recipient_brand_sender_and_no_bcc(self):
        self.post_statement()
        self.assertEqual(len(mail.outbox), 1)
        self.assert_message_is_local(
            mail.outbox[0],
            [self.parent.personal_email],
            "alpha-sender-default@example.test",
        )

    def test_preview_uses_local_brand_sender_and_no_bcc(self):
        self.post_statement(test_email="preview@example.test")
        self.assertEqual(len(mail.outbox), 1)
        self.assert_message_is_local(
            mail.outbox[0],
            ["preview@example.test"],
            "alpha-sender-default@example.test",
        )

    def test_local_named_sender_works_for_actual_and_preview(self):
        for preview in (False, True):
            with self.subTest(preview=preview):
                mail.outbox = []
                extra = {"test_email": "preview@example.test"} if preview else {}
                self.post_statement(from_account="alpha-sender", **extra)
                self.assertEqual(len(mail.outbox), 1)
                self.assertIn("alpha-sender@example.test", mail.outbox[0].from_email)
                self.assertEqual(
                    mail.outbox[0].to,
                    ["preview@example.test" if preview else self.parent.personal_email],
                )

    def test_local_assignment_payment_and_sliding_scale_remain_usable(self):
        FeeAssignment.objects.create(
            organization=self.alpha, fee=self.fee, student=self.student
        )
        Payment.objects.create(
            organization=self.alpha,
            program=self.program,
            student=self.student,
            amount=Decimal("25.00"),
            paid_on=timezone.localdate(),
            paid_via="cash",
        )
        SlidingScale.objects.create(
            organization=self.alpha,
            student=self.student,
            status=SlidingScale.STATUS_APPROVED,
            percent=Decimal("20.00"),
        )
        mail.outbox = []
        response = self.client.get(self.url(), HTTP_HOST=self.host)
        self.assertContains(response, "Alpha Student ($75.00)")
        self.post_statement(recipient_filter="individual", student=str(self.student.pk))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.parent.personal_email])
        self.assertIn("75.00", mail.outbox[0].alternatives[0][0])

    def test_unconfigured_tenant_can_use_platform_default_sender(self):
        OrganizationSettings.objects.filter(organization=self.alpha).update(
            data={"email_settings": {}}
        )
        self.post_statement()
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("platform@example.test", mail.outbox[0].from_email)

    def test_foreign_adult_and_relationship_owner_are_not_recipients(self):
        foreign_rel = AdultStudentRelationship.objects.create(
            organization=self.alpha, student=self.student, adult=self.foreign_parent
        )
        self.student.primary_contact_relationship = foreign_rel
        self.student.save(update_fields=["primary_contact_relationship"])
        wrong_owner = self.make_parent(self.alpha, "wrong-owner@example.test")
        AdultStudentRelationship.objects.create(
            organization=self.beta, student=self.student, adult=wrong_owner
        )
        mail.outbox = []
        self.post_statement()
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.parent.personal_email])

    def assert_unsafe_financial_data_is_unavailable(self):
        with self.subTest(check="choice label"):
            response = self.client.get(self.url(), HTTP_HOST=self.host)
            field = response.context["form"].fields["student"]
            label = field.label_from_instance(self.student)
            self.assertIn("unavailable", label.lower())
            self.assertNotIn("$", label)
            self.assertNotIn("Local tuition", label)
        for recipient_filter in ("all", "non_zero", "positive", "individual"):
            for preview in (False, True):
                with self.subTest(recipient_filter=recipient_filter, preview=preview):
                    mail.outbox = []
                    extra = {"student": str(self.student.pk)}
                    if preview:
                        extra["test_email"] = "preview@example.test"
                    self.post_statement(recipient_filter=recipient_filter, **extra)
                    self.assertEqual(mail.outbox, [])

    def test_foreign_owned_fee_makes_balance_unavailable(self):
        Fee.objects.filter(pk=self.fee.pk).update(organization=self.beta)
        self.assert_unsafe_financial_data_is_unavailable()

    def test_foreign_owned_assignment_makes_balance_unavailable(self):
        FeeAssignment.objects.create(
            organization=self.beta, fee=self.fee, student=self.student
        )
        self.assert_unsafe_financial_data_is_unavailable()

    def test_assignment_to_foreign_student_makes_balance_unavailable(self):
        FeeAssignment.objects.create(
            organization=self.alpha, fee=self.fee, student=self.foreign_student
        )
        self.assert_unsafe_financial_data_is_unavailable()

    def test_assignment_to_unenrolled_local_student_makes_balance_unavailable(self):
        student = Student.objects.create(
            organization=self.alpha, legal_first_name="Unenrolled", last_name="Student"
        )
        FeeAssignment.objects.create(
            organization=self.alpha, fee=self.fee, student=student
        )
        self.assert_unsafe_financial_data_is_unavailable()

    def test_assignment_with_foreign_owned_enrollment_makes_balance_unavailable(self):
        student = Student.objects.create(
            organization=self.alpha,
            legal_first_name="Wrong Enrollment",
            last_name="Student",
        )
        Enrollment.objects.create(
            organization=self.beta, program=self.program, student=student, active=True
        )
        FeeAssignment.objects.create(
            organization=self.alpha, fee=self.fee, student=student
        )
        self.assert_unsafe_financial_data_is_unavailable()

    def test_foreign_owned_payment_makes_balance_unavailable(self):
        Payment.objects.create(
            organization=self.beta,
            program=self.program,
            student=self.student,
            amount=Decimal("25.00"),
            paid_on=timezone.localdate(),
            paid_via="cash",
        )
        mail.outbox = []
        self.assert_unsafe_financial_data_is_unavailable()

    def test_foreign_owned_approved_sliding_scale_makes_balance_unavailable(self):
        SlidingScale.objects.create(
            organization=self.beta,
            student=self.student,
            status=SlidingScale.STATUS_APPROVED,
            percent=Decimal("50.00"),
        )
        mail.outbox = []
        self.assert_unsafe_financial_data_is_unavailable()

    def test_missing_organization_context_is_rejected(self):
        for method in ("GET", "POST"):
            with self.subTest(method=method):
                factory = RequestFactory()
                data = {
                    "program": str(self.program.pk),
                    "subject": "Balance statement",
                    "recipient_filter": "all",
                    "from_account": "DEFAULT",
                }
                request = getattr(factory, method.lower())(self.url(), data)
                request.user = self.user
                request.organization = None
                request.session = {}
                request._messages = FallbackStorage(request)
                mail.outbox = []
                try:
                    response = ProgramEmailBalancesView.as_view()(
                        request, pk=self.program.pk
                    )
                except Http404:
                    continue
                self.assertNotIn(b"Alpha Student", response.content)
                self.assertEqual(mail.outbox, [])

    def test_non_lead_role_cannot_view_or_send(self):
        user = User.objects.create_user(username="balance-parent")
        UserOrganizationMembership.objects.create(
            organization=self.alpha, user=user, role="Parent", is_active=True
        )
        self.client.force_login(user)
        response = self.client.get(self.url(), HTTP_HOST=self.host)
        self.assertNotIn(b"Alpha Student ($125.00)", response.content)
        self.post_statement()
        self.assertEqual(mail.outbox, [])
