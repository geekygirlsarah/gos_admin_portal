"""Test-first regressions for scheduled tenant-isolated balance statements."""

import datetime
from decimal import Decimal
from io import StringIO

from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from organizations.models import Organization, OrganizationSettings
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
    MAILERS={"default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}},
    DEFAULT_FROM_EMAIL="platform@example.test",
    NOTIFICATION_FROM_EMAIL=None,
    TESTING=True,
)
class MonthlyBalancesTenantIsolationTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.alpha = Organization.objects.create(name="Monthly Alpha", slug="monthly-a")
        self.beta = Organization.objects.create(name="Monthly Beta", slug="monthly-b")
        for org, key, brand in (
            (self.alpha, "alpha", "Alpha Monthly Brand"),
            (self.beta, "beta", "Beta Monthly Brand"),
        ):
            OrganizationSettings.objects.update_or_create(
                organization=org,
                defaults={
                    "data": {
                        "email_settings": {
                            "organization_name": brand,
                            "notification_from_email": f"{key}-billing@example.test",
                            "reply_to_email": f"{key}-reply@example.test",
                        }
                    }
                },
            )

        self.alpha_program = self.make_program(self.alpha, "Alpha Robotics", "125.00")
        self.beta_program = self.make_program(self.beta, "Beta Science", "80.00")
        self.alpha_student, self.alpha_parent, self.alpha_rel = self.make_family(
            self.alpha, self.alpha_program, "Alpha", "alpha-parent@example.test"
        )
        self.beta_student, self.beta_parent, self.beta_rel = self.make_family(
            self.beta, self.beta_program, "Beta", "beta-parent@example.test"
        )
        mail.outbox = []

    def make_program(self, organization, name, amount):
        program = Program.objects.create(
            organization=organization, name=name, active=True
        )
        Fee.objects.create(
            organization=organization,
            program=program,
            name=f"{name} fee",
            amount=Decimal(amount),
            effective_date=self.today - datetime.timedelta(days=60),
        )
        return program

    def make_family(self, organization, program, name, email):
        student = Student.objects.create(
            organization=organization, legal_first_name=name, last_name="Student"
        )
        Enrollment.objects.create(
            organization=organization, student=student, program=program, active=True
        )
        parent = self.make_parent(organization, email)
        relationship = AdultStudentRelationship.objects.create(
            organization=organization, student=student, adult=parent
        )
        return student, parent, relationship

    def make_parent(self, organization, email, email_updates=True, login_enabled=True):
        return Adult.objects.create(
            organization=organization,
            legal_first_name="Guardian",
            last_name="Contact",
            personal_email=email,
            is_parent=True,
            email_updates=email_updates,
            login_enabled=login_enabled,
        )

    def run_job(self, **options):
        mail.outbox = []
        output = StringIO()
        call_command("send_monthly_balances", stdout=output, **options)
        return list(mail.outbox), output.getvalue()

    def messages_by_recipient(self, messages):
        self.assertTrue(all(len(message.to) == 1 for message in messages))
        return {message.to[0]: message for message in messages}

    def test_all_organizations_have_separate_messages_branding_and_senders(self):
        messages, _ = self.run_job()
        by_recipient = self.messages_by_recipient(messages)
        self.assertEqual(len(messages), 2)
        self.assertEqual(
            set(by_recipient),
            {self.alpha_parent.personal_email, self.beta_parent.personal_email},
        )
        for email, own, other, student, foreign, sender, reply in (
            (
                self.alpha_parent.personal_email,
                "Alpha Monthly Brand",
                "Beta Monthly Brand",
                "Alpha Student",
                "Beta Student",
                "alpha-billing@example.test",
                "alpha-reply@example.test",
            ),
            (
                self.beta_parent.personal_email,
                "Beta Monthly Brand",
                "Alpha Monthly Brand",
                "Beta Student",
                "Alpha Student",
                "beta-billing@example.test",
                "beta-reply@example.test",
            ),
        ):
            message = by_recipient[email]
            html = message.alternatives[0][0]
            for label, check in (
                ("branding", lambda: self.assertIn(own, html)),
                ("foreign branding", lambda: self.assertNotIn(other, html)),
                ("sender", lambda: self.assertIn(sender, message.from_email)),
                ("reply-to", lambda: self.assertEqual(message.reply_to, [reply])),
                ("student", lambda: self.assertIn(student, html)),
                ("foreign student", lambda: self.assertNotIn(foreign, html)),
                ("bcc", lambda: self.assertEqual(message.bcc, [])),
            ):
                with self.subTest(recipient=email, check=label):
                    check()

    def test_inactive_organization_does_not_receive_balance_email(self):
        self.alpha.is_active = False
        self.alpha.save(update_fields=["is_active"])

        messages, _ = self.run_job()

        self.assertEqual(
            set(self.messages_by_recipient(messages)), {self.beta_parent.personal_email}
        )

    def test_only_opted_in_local_adults_receive_balance_emails(self):
        opted_out = self.make_parent(
            self.alpha, "opted-out@example.test", email_updates=False
        )
        AdultStudentRelationship.objects.create(
            organization=self.alpha,
            student=self.alpha_student,
            adult=opted_out,
        )

        messages, _ = self.run_job(student_id=self.alpha_student.pk)

        self.assertEqual(
            {message.to[0] for message in messages}, {self.alpha_parent.personal_email}
        )

    def test_local_multi_program_total_and_financial_records(self):
        second = self.make_program(self.alpha, "Alpha Art", "75.00")
        Enrollment.objects.create(
            organization=self.alpha, student=self.alpha_student, program=second
        )
        FeeAssignment.objects.create(
            organization=self.alpha, student=self.alpha_student, fee=second.fees.get()
        )
        Payment.objects.create(
            organization=self.alpha,
            student=self.alpha_student,
            program=second,
            amount=Decimal("20.00"),
            paid_on=self.today,
            paid_via="cash",
        )
        SlidingScale.objects.create(
            organization=self.alpha,
            student=self.alpha_student,
            status=SlidingScale.STATUS_APPROVED,
            percent=Decimal("20.00"),
        )
        messages, _ = self.run_job(student_id=self.alpha_student.pk)
        self.assertEqual(len(messages), 2)
        self.assertTrue(
            all(
                message.to == [self.alpha_parent.personal_email] for message in messages
            )
        )
        for program_name, other_name, balance in (
            ("Alpha Robotics", "Alpha Art", "$100.00"),
            ("Alpha Art", "Alpha Robotics", "$40.00"),
        ):
            with self.subTest(program=program_name):
                message = next(m for m in messages if program_name in m.subject)
                html = message.alternatives[0][0]
                self.assertIn(program_name, html)
                self.assertNotIn(other_name, html)
                self.assertIn(balance, html)
                self.assertNotIn("$140.00", html)

    def test_program_filters_apply_independently_for_same_student(self):
        credit_program = self.make_program(self.alpha, "Alpha Credit", "75.00")
        credit_program.fees.update(
            effective_date=self.today - datetime.timedelta(days=60)
        )
        Enrollment.objects.create(
            organization=self.alpha, student=self.alpha_student, program=credit_program
        )
        Payment.objects.create(
            organization=self.alpha,
            student=self.alpha_student,
            program=credit_program,
            amount=Decimal("200.00"),
            paid_on=self.today - datetime.timedelta(days=60),
            paid_via="cash",
        )

        messages, _ = self.run_job(student_id=self.alpha_student.pk)
        self.assertEqual(len(messages), 1)
        self.assertIn("Alpha Robotics", messages[0].subject)
        self.assertNotIn("Alpha Credit", messages[0].body)
        messages, _ = self.run_job(student_id=self.alpha_student.pk, all_balances=True)
        self.assertEqual(len(messages), 2)
        self.assertTrue(any("Alpha Credit" in message.subject for message in messages))

    def test_missing_branding_uses_momentum_and_neutral_payment_guidance(self):
        OrganizationSettings.objects.filter(organization=self.alpha).update(data={})

        messages, _ = self.run_job(student_id=self.alpha_student.pk)

        self.assertEqual(len(messages), 1)
        message = messages[0]
        html = message.alternatives[0][0]
        self.assertIn("Momentum", html)
        self.assertIn("Momentum", message.from_email)
        self.assertIn("platform@example.test", message.from_email)
        self.assertIn("contact the program", html.lower())
        for forbidden in (
            "Girls of Steel",
            "Carnegie Mellon",
            "Elizabeth Kysel",
            "Sarah Withee",
            "girlsofsteel.org",
            "girlsofsteelrobotics.org",
            "cashnet.com",
        ):
            self.assertNotIn(forbidden, html)

    def test_saved_balance_branding_is_preserved(self):
        org_settings = OrganizationSettings.objects.get(organization=self.alpha)
        org_settings.data["email_settings"].update(
            address_line1="Alpha Address",
            contact_name="Alpha Contact",
            footer_text="Alpha Footer",
        )
        org_settings.save(update_fields=["data"])

        messages, _ = self.run_job(student_id=self.alpha_student.pk)

        self.assertEqual(len(messages), 1)
        self.assertIn("Alpha Monthly Brand", messages[0].from_email)
        html = messages[0].alternatives[0][0]
        for value in (
            "Alpha Monthly Brand",
            "Alpha Address",
            "Alpha Contact",
            "Alpha Footer",
        ):
            with self.subTest(value=value):
                self.assertIn(value, html)

    def test_mismatched_enrollments_do_not_add_programs_to_statements(self):
        wrong_owner = self.make_program(self.alpha, "Wrong Owner Program", "31.00")
        Enrollment.objects.create(
            organization=self.beta, student=self.alpha_student, program=wrong_owner
        )
        Enrollment.objects.create(
            organization=self.alpha,
            student=self.alpha_student,
            program=self.beta_program,
        )
        Enrollment.objects.create(
            organization=self.alpha,
            student=self.beta_student,
            program=self.alpha_program,
        )
        with self.assertLogs(
            "programs.services.balances_digest", level="WARNING"
        ) as logs:
            messages, _ = self.run_job()
        self.assertIn("enrollment", " ".join(logs.output).lower())
        self.assertNotIn("Alpha Student", " ".join(logs.output))
        by_recipient = self.messages_by_recipient(messages)
        self.assertEqual(
            set(by_recipient),
            {self.alpha_parent.personal_email, self.beta_parent.personal_email},
        )
        for email, forbidden in (
            (
                self.alpha_parent.personal_email,
                ("Wrong Owner Program", "Beta Science", "$236.00"),
            ),
            (self.beta_parent.personal_email, ("Alpha Robotics", "$205.00")),
        ):
            html = by_recipient[email].alternatives[0][0]
            for marker in forbidden:
                with self.subTest(email=email, marker=marker):
                    self.assertNotIn(marker, html)
        for email, expected in (
            (self.alpha_parent.personal_email, r"Balance Due:\s*\$125\.00"),
            (self.beta_parent.personal_email, r"Balance Due:\s*\$80\.00"),
        ):
            with self.subTest(email=email, check="total"):
                self.assertRegex(by_recipient[email].body, expected)

    def test_student_with_only_foreign_owned_or_foreign_program_enrollment_is_excluded(
        self,
    ):
        invalid = Student.objects.create(
            organization=self.alpha, legal_first_name="Invalid", last_name="Student"
        )
        parent = self.make_parent(self.alpha, "invalid@example.test")
        AdultStudentRelationship.objects.create(
            organization=self.alpha, student=invalid, adult=parent
        )
        Enrollment.objects.create(
            organization=self.beta, student=invalid, program=self.alpha_program
        )
        Enrollment.objects.create(
            organization=self.alpha, student=invalid, program=self.beta_program
        )
        for all_balances in (False, True):
            with self.subTest(all_balances=all_balances):
                messages, _ = self.run_job(all_balances=all_balances)
                self.assertEqual(
                    set(self.messages_by_recipient(messages)),
                    {self.alpha_parent.personal_email, self.beta_parent.personal_email},
                )

    def test_foreign_adult_and_wrong_relationship_owner_are_not_recipients(self):
        foreign = self.make_parent(self.beta, "foreign-contact@example.test")
        AdultStudentRelationship.objects.create(
            organization=self.alpha, student=self.alpha_student, adult=foreign
        )
        wrong_owner = self.make_parent(self.alpha, "wrong-owner@example.test")
        AdultStudentRelationship.objects.create(
            organization=self.beta, student=self.alpha_student, adult=wrong_owner
        )
        messages, _ = self.run_job()
        self.assertEqual(len(messages), 2)
        self.assertEqual(
            set(self.messages_by_recipient(messages)),
            {self.alpha_parent.personal_email, self.beta_parent.personal_email},
        )

    def test_foreign_or_unrelated_primary_contact_is_not_used_as_fallback(self):
        self.alpha_parent.email_updates = False
        self.alpha_parent.save(update_fields=["email_updates"])
        foreign = self.make_parent(
            self.beta, "foreign-fallback@example.test", email_updates=False
        )
        foreign_rel = AdultStudentRelationship.objects.create(
            organization=self.alpha, student=self.alpha_student, adult=foreign
        )
        unrelated = self.make_parent(
            self.alpha, "unrelated-fallback@example.test", email_updates=False
        )
        unrelated_rel = AdultStudentRelationship.objects.create(
            organization=self.alpha, student=self.beta_student, adult=unrelated
        )
        wrong_owner = self.make_parent(
            self.alpha, "wrong-owner-fallback@example.test", email_updates=False
        )
        wrong_owner_rel = AdultStudentRelationship.objects.create(
            organization=self.beta, student=self.alpha_student, adult=wrong_owner
        )
        for relationship in (foreign_rel, unrelated_rel, wrong_owner_rel):
            with self.subTest(relationship=relationship.pk):
                Student.objects.filter(pk=self.alpha_student.pk).update(
                    primary_contact_relationship=relationship
                )
                messages, _ = self.run_job(student_id=self.alpha_student.pk)
                self.assertEqual(messages, [])

    def test_local_primary_fallback_and_disabled_guardian_policy(self):
        self.alpha_parent.email_updates = False
        self.alpha_parent.save(update_fields=["email_updates"])
        Student.objects.filter(pk=self.alpha_student.pk).update(
            primary_contact_relationship=self.alpha_rel
        )
        disabled = self.make_parent(
            self.alpha, "disabled@example.test", login_enabled=False
        )
        AdultStudentRelationship.objects.create(
            organization=self.alpha, student=self.alpha_student, adult=disabled
        )
        messages, _ = self.run_job(student_id=self.alpha_student.pk)
        self.assertEqual(messages, [])

    def assert_affected_statement_suppressed(self, all_alpha=False):
        for all_balances in (False, True):
            with self.subTest(all_balances=all_balances):
                with self.assertLogs(
                    "programs.services.balances_digest", level="WARNING"
                ) as logs:
                    messages, _ = self.run_job(all_balances=all_balances)
                self.assertNotIn("Alpha Student", " ".join(logs.output))
                by_recipient = self.messages_by_recipient(messages)
                expected = {self.beta_parent.personal_email}
                if not all_alpha:
                    expected.add(self.alpha_parent.personal_email)
                self.assertEqual(set(by_recipient), expected)
                self.assertNotIn(
                    "Alpha Student", by_recipient[self.beta_parent.personal_email].body
                )
                if not all_alpha:
                    safe_message = by_recipient[self.alpha_parent.personal_email]
                    self.assertIn("Alpha Robotics", safe_message.body)
                    self.assertNotIn("Alpha Second", safe_message.body)
                    self.assertIn("$125.00", safe_message.body)

    def second_alpha_program(self):
        program = self.make_program(self.alpha, "Alpha Second", "75.00")
        Enrollment.objects.create(
            organization=self.alpha, student=self.alpha_student, program=program
        )
        return program

    def test_foreign_fee_suppresses_entire_multi_program_statement(self):
        """The inconsistent program is blocked; another program remains sendable."""
        program = self.second_alpha_program()
        Fee.objects.filter(program=program).update(organization=self.beta)
        self.assert_affected_statement_suppressed()

    def test_foreign_assignment_suppresses_entire_multi_program_statement(self):
        """The affected program is blocked; another program remains sendable."""
        program = self.second_alpha_program()
        FeeAssignment.objects.create(
            organization=self.beta, fee=program.fees.get(), student=self.alpha_student
        )
        self.assert_affected_statement_suppressed()

    def test_assignment_to_foreign_student_suppresses_entire_statement(self):
        program = self.second_alpha_program()
        FeeAssignment.objects.create(
            organization=self.alpha, fee=program.fees.get(), student=self.beta_student
        )
        self.assert_affected_statement_suppressed()

    def test_assignment_with_mismatched_student_enrollment_suppresses_statement(self):
        program = self.second_alpha_program()
        assigned = Student.objects.create(
            organization=self.alpha, legal_first_name="Assigned", last_name="Student"
        )
        Enrollment.objects.create(
            organization=self.beta, student=assigned, program=program
        )
        FeeAssignment.objects.create(
            organization=self.alpha, fee=program.fees.get(), student=assigned
        )
        self.assert_affected_statement_suppressed()

    def test_foreign_payment_suppresses_entire_multi_program_statement(self):
        """The affected program is blocked; another program remains sendable."""
        program = self.second_alpha_program()
        Payment.objects.create(
            organization=self.beta,
            student=self.alpha_student,
            program=program,
            amount=Decimal("20.00"),
            paid_on=self.today,
            paid_via="cash",
        )
        self.assert_affected_statement_suppressed()

    def test_foreign_approved_applicable_scale_suppresses_entire_statement(self):
        self.second_alpha_program()
        SlidingScale.objects.create(
            organization=self.beta,
            student=self.alpha_student,
            status=SlidingScale.STATUS_APPROVED,
            percent=Decimal("50.00"),
        )
        self.assert_affected_statement_suppressed(all_alpha=True)

    def test_foreign_pending_or_nonoverlapping_scale_does_not_block_statement(self):
        SlidingScale.objects.create(
            organization=self.beta,
            student=self.alpha_student,
            status=SlidingScale.STATUS_PENDING,
            percent=Decimal("50.00"),
        )
        SlidingScale.objects.create(
            organization=self.beta,
            student=self.alpha_student,
            status=SlidingScale.STATUS_APPROVED,
            percent=Decimal("50.00"),
            date=self.today - datetime.timedelta(days=200),
            expiration_date=self.today - datetime.timedelta(days=100),
        )
        messages, _ = self.run_job(student_id=self.alpha_student.pk)
        self.assertEqual([m.to for m in messages], [[self.alpha_parent.personal_email]])
        self.assertIn("$125.00", messages[0].body)

    def test_command_preserves_positive_recent_and_all_balances_filters(self):
        recent_program = self.make_program(self.alpha, "Recent Paid", "50.00")
        recent, recent_parent, _ = self.make_family(
            self.alpha, recent_program, "Recent", "recent@example.test"
        )
        old_program = self.make_program(self.beta, "Old Paid", "40.00")
        old, old_parent, _ = self.make_family(
            self.beta, old_program, "Old", "old@example.test"
        )
        for student, org, program, amount, days in (
            (recent, self.alpha, recent_program, "50.00", 3),
            (old, self.beta, old_program, "40.00", 60),
        ):
            Payment.objects.create(
                organization=org,
                student=student,
                program=program,
                amount=Decimal(amount),
                paid_on=self.today - datetime.timedelta(days=days),
                paid_via="cash",
            )
        messages, _ = self.run_job(date=self.today.isoformat(), days_back=10)
        self.assertEqual(
            set(self.messages_by_recipient(messages)),
            {
                self.alpha_parent.personal_email,
                self.beta_parent.personal_email,
                recent_parent.personal_email,
            },
        )
        messages, _ = self.run_job(date=self.today.isoformat(), days_back=2)
        self.assertEqual(
            set(self.messages_by_recipient(messages)),
            {self.alpha_parent.personal_email, self.beta_parent.personal_email},
        )
        messages, _ = self.run_job(
            all_balances=True, date=self.today.isoformat(), days_back=2
        )
        self.assertEqual(
            set(self.messages_by_recipient(messages)),
            {
                self.alpha_parent.personal_email,
                self.beta_parent.personal_email,
                recent_parent.personal_email,
                old_parent.personal_email,
            },
        )

    def test_command_student_email_and_dry_run_filters(self):
        messages, _ = self.run_job(student_id=self.beta_student.pk)
        self.assertEqual([m.to for m in messages], [[self.beta_parent.personal_email]])
        messages, _ = self.run_job(email=self.alpha_parent.personal_email.upper())
        self.assertEqual([m.to for m in messages], [[self.alpha_parent.personal_email]])
        messages, output = self.run_job(dry_run=True, student_id=self.alpha_student.pk)
        self.assertEqual(messages, [])
        self.assertIn("1 balance statement(s) would be sent", output)
        self.assertNotIn(self.alpha_parent.personal_email, output)
        self.assertNotIn(self.beta_parent.personal_email, output)
        self.assertNotIn(self.alpha_student.display_name, output)
        self.assertNotIn(f"student_id={self.alpha_student.pk}", output)
        self.assertNotIn("$125", output)
