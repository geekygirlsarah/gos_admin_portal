import json
from datetime import timedelta
from io import StringIO
from unittest import mock

from allauth.core.context import request_context
from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.mail import EmailMultiAlternatives
from django.core.mail.backends.locmem import EmailBackend as LocmemBackend
from django.core.management import call_command
from django.db.models import TextField
from django.db.models.functions import Cast
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from applications.models import Application
from applications.services import (
    send_application_submitted_email,
)
from applications.services import send_otp_email as send_application_otp_email
from applications.services import (
    send_parent_handoff_email,
)
from audit.events import AuditEvent
from audit.models import AuditLog
from GoSAdminPortal.adapter import AccountAdapter
from guest_forms.models import GuestForm, GuestFormSubmission
from guest_forms.services import send_submission_confirmation_email
from organizations.models import Organization, UserOrganizationMembership
from programs.models import (
    EmailHistory,
    EmailHistoryAddress,
    EmailHistoryBatch,
)
from programs.utils.email_async import send_messages_in_background
from programs.utils.email_history import (
    mark_email_history,
    mark_email_history_attempted,
)

_FILE_ENCRYPTION_KEY = Fernet.generate_key().decode()


@override_settings(
    ALLOWED_HOSTS=[".localhost", "testserver"],
    FILE_ENCRYPTION_KEY=_FILE_ENCRYPTION_KEY,
    MOMENTUM_ROOT_DOMAIN="localhost",
)
class EmailHistoryTests(TestCase):
    def setUp(self):
        mail.outbox = []
        self.alpha = Organization.objects.create(
            name="History Alpha", slug="history-alpha", subdomain="history-alpha"
        )
        self.beta = Organization.objects.create(
            name="History Beta", slug="history-beta", subdomain="history-beta"
        )
        user_model = get_user_model()
        self.alpha_lead = user_model.objects.create_user(
            username="history-alpha-lead",
            email="alpha-lead@example.test",
            password="password123",
        )  # nosec B106
        self.beta_lead = user_model.objects.create_user(
            username="history-beta-lead",
            email="beta-lead@example.test",
            password="password123",
        )  # nosec B106
        UserOrganizationMembership.objects.create(
            user=self.alpha_lead,
            organization=self.alpha,
            role="LeadMentor",
            is_active=True,
        )
        UserOrganizationMembership.objects.create(
            user=self.beta_lead,
            organization=self.beta,
            role="LeadMentor",
            is_active=True,
        )

    def _send_history_message(
        self, organization, recipient, *, category="program_message", body=True
    ):
        message = EmailMultiAlternatives(
            subject="Balance reminder",
            body="The personalized message body",
            from_email="sender@example.test",
            to=[recipient],
        )
        message.attach_alternative("<p>The personalized message body</p>", "text/html")
        mark_email_history(
            message,
            organization,
            category=category,
            initiated_by=self.alpha_lead if organization == self.alpha else None,
            retain_body=body,
        )
        send_messages_in_background([message], connection=LocmemBackend(), sync=True)
        return (
            EmailHistory.objects.filter(batch__organization=organization)
            .order_by("-pk")
            .first()
        )

    def test_async_send_records_encrypted_per_recipient_rendered_message(self):
        record = self._send_history_message(
            self.alpha, "student@example.test", category="mail_merge"
        )

        self.assertEqual(record.status, EmailHistory.STATUS_ACCEPTED)
        self.assertEqual(record.batch.category, "mail_merge")
        self.assertEqual(record.batch.initiated_by, self.alpha_lead)
        self.assertEqual(record.subject, "Balance reminder")
        self.assertEqual(record.body_text, "The personalized message body")
        self.assertEqual(record.body_html, "<p>The personalized message body</p>")
        address = EmailHistoryAddress.objects.get(history=record, kind="to")
        self.assertEqual(address.email_address, "student@example.test")

        stored_body = (
            EmailHistory.objects.filter(pk=record.pk)
            .annotate(raw_body=Cast("body_text", output_field=TextField()))
            .values_list("raw_body", flat=True)
            .get()
        )
        stored_address = (
            EmailHistoryAddress.objects.filter(pk=address.pk)
            .annotate(raw_address=Cast("email_address", output_field=TextField()))
            .values_list("raw_address", flat=True)
            .get()
        )
        self.assertNotIn("personalized message body", stored_body)
        self.assertNotIn("student@example.test", stored_address)

    def test_otp_history_retains_safe_metadata_but_no_subject_or_body(self):
        record = self._send_history_message(
            self.alpha, "student@example.test", category="otp", body=False
        )

        self.assertEqual(record.status, EmailHistory.STATUS_ACCEPTED)
        self.assertIsNone(record.subject)
        self.assertIsNone(record.body_text)
        self.assertIsNone(record.body_html)

    def test_application_otp_and_handoff_history_omit_credentials(self):
        application = Application.objects.create(
            organization=self.alpha, email="applicant@example.test"
        )
        send_application_otp_email(application, "123456")
        otp_record = EmailHistory.objects.get(batch__organization=self.alpha)

        self.assertEqual(otp_record.batch.category, "otp")
        self.assertFalse(otp_record.body_retained)
        self.assertIsNone(otp_record.subject)
        self.assertIsNone(otp_record.body_text)
        self.assertIsNone(otp_record.body_html)
        self.assertNotIn("123456", otp_record.subject or "")

        application.issue_handoff_token()
        send_parent_handoff_email(application, "parent@example.test")
        handoff_record = EmailHistory.objects.order_by("-pk").first()

        self.assertEqual(handoff_record.batch.category, "application_handoff")
        self.assertFalse(handoff_record.body_retained)
        self.assertIsNone(handoff_record.subject)
        self.assertIsNone(handoff_record.body_text)
        self.assertIsNone(handoff_record.body_html)
        self.assertNotIn(application.handoff_token, handoff_record.body_text or "")

    def test_application_notification_retains_rendered_body_and_initiator(self):
        application = Application.objects.create(
            organization=self.alpha, email="applicant@example.test"
        )
        request = RequestFactory().get("/", HTTP_HOST="history-alpha.localhost")
        request.organization = self.alpha
        request.user = self.alpha_lead

        send_application_submitted_email(application, request)
        record = EmailHistory.objects.get(batch__organization=self.alpha)

        self.assertEqual(record.batch.category, "application_notification")
        self.assertEqual(record.batch.initiated_by, self.alpha_lead)
        self.assertTrue(record.body_retained)
        self.assertIn("Thanks for applying", record.body_text)
        self.assertIn("Thanks for applying", record.body_html)

    def test_guest_form_confirmation_is_recorded_for_the_form_organization(self):
        guest_form = GuestForm.objects.create(
            organization=self.alpha,
            slug="history-guest-form",
            name="Guest Release",
            file=SimpleUploadedFile(
                "guest-form.pdf", b"pdf", content_type="application/pdf"
            ),
        )
        submission = GuestFormSubmission.objects.create(
            guest_form=guest_form,
            participant_first_name="Guest",
            participant_last_name="Tester",
            email="guest@example.test",
            file=SimpleUploadedFile(
                "signed.pdf", b"pdf", content_type="application/pdf"
            ),
        )

        send_submission_confirmation_email(submission)

        record = EmailHistory.objects.get(batch__organization=self.alpha)
        self.assertEqual(record.batch.category, "guest_form_confirmation")
        self.assertTrue(record.body_retained)
        self.assertIn("Guest Release", record.subject)
        self.assertIn("Guest Tester", record.body_text)
        self.assertEqual(
            EmailHistoryAddress.objects.get(history=record, kind="to").email_address,
            "guest@example.test",
        )

    def test_status_updates_are_scoped_to_the_message_organization(self):
        foreign_batch = EmailHistoryBatch.objects.create(
            organization=self.beta, category="foreign"
        )
        foreign_record = EmailHistory.objects.create(
            organization=self.beta,
            batch=foreign_batch,
            status=EmailHistory.STATUS_QUEUED,
        )
        message = EmailMultiAlternatives(
            subject="Scoped status",
            body="body",
            from_email="sender@example.test",
            to=["recipient@example.test"],
        )
        mark_email_history(message, self.alpha, category="ordinary")
        setattr(message, "_momentum_email_history_id", foreign_record.pk)

        mark_email_history_attempted([message])

        foreign_record.refresh_from_db()
        self.assertEqual(foreign_record.status, EmailHistory.STATUS_QUEUED)

    @override_settings(EMAIL_ASYNC=False)
    def test_account_adapter_login_code_records_metadata_only(self):
        request = RequestFactory().get("/", HTTP_HOST="history-alpha.localhost")
        request.organization = self.alpha
        request.user = get_user_model().objects.get(pk=self.alpha_lead.pk)

        with request_context(request):
            AccountAdapter().send_mail(
                "account/email/login_code",
                "account-user@example.test",
                {"code": "654321"},
            )

        record = EmailHistory.objects.get(batch__organization=self.alpha)

        self.assertEqual(record.batch.category, "otp")
        self.assertEqual(record.status, EmailHistory.STATUS_UNKNOWN)
        self.assertFalse(record.body_retained)
        self.assertIsNone(record.subject)
        self.assertIsNone(record.body_text)
        self.assertIsNone(record.body_html)
        self.assertEqual(record.batch.category, "otp")
        self.assertTrue(
            EmailHistoryAddress.objects.filter(history=record, kind="to").exists()
        )

    def test_ambiguous_dispatch_outcome_is_not_reported_as_accepted(self):
        message = EmailMultiAlternatives(
            subject="Ambiguous",
            body="Message",
            from_email="sender@example.test",
            to=["student@example.test"],
        )
        mark_email_history(message, self.alpha, category="program_message")
        backend = mock.MagicMock()
        backend.send_messages.return_value = 0

        send_messages_in_background([message], connection=backend, sync=True)

        self.assertEqual(
            EmailHistory.objects.get(batch__organization=self.alpha).status,
            EmailHistory.STATUS_UNKNOWN,
        )

    def test_history_is_limited_to_leads_and_organization(self):
        alpha_record = self._send_history_message(
            self.alpha, "alpha-student@example.test"
        )
        beta_record = self._send_history_message(
            self.beta, "beta-student@example.test", body=False
        )
        EmailHistoryAddress.objects.create(
            organization=self.beta,
            history=alpha_record,
            kind=EmailHistoryAddress.KIND_BCC,
            email_address="foreign-address@example.test",
            email_fingerprint="f" * 64,
        )
        foreign_batch = EmailHistoryBatch.objects.create(
            organization=self.beta, category="foreign"
        )
        mismatched_record = EmailHistory.objects.create(
            organization=self.alpha,
            batch=foreign_batch,
            subject="foreign batch subject",
        )
        self.client.force_login(self.alpha_lead)

        response = self.client.get(
            reverse("email_history_list"), HTTP_HOST="history-alpha.localhost"
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "alpha-student@example.test")
        self.assertNotContains(response, "beta-student@example.test")
        denied = self.client.get(
            reverse("email_history_detail", args=[beta_record.pk]),
            HTTP_HOST="history-alpha.localhost",
        )
        self.assertEqual(denied.status_code, 302)
        self.assertEqual(denied["Location"], reverse("home"))
        alpha_detail = self.client.get(
            reverse("email_history_detail", args=[alpha_record.pk]),
            HTTP_HOST="history-alpha.localhost",
        )
        self.assertNotContains(alpha_detail, "foreign-address@example.test")
        mismatched = self.client.get(
            reverse("email_history_detail", args=[mismatched_record.pk]),
            HTTP_HOST="history-alpha.localhost",
        )
        self.assertEqual(mismatched.status_code, 302)
        self.assertEqual(mismatched["Location"], reverse("home"))
        self.assertTrue(
            AuditLog.objects.filter(
                event=AuditEvent.SENSITIVE_DATA_VIEW,
                resource_type="EmailHistory",
                resource_id=str(alpha_record.pk),
            ).exists()
        )

    def test_exact_recipient_search_uses_fingerprint_and_non_lead_is_denied(self):
        self._send_history_message(self.alpha, "find-this@example.test")
        self.client.force_login(self.alpha_lead)

        found = self.client.get(
            reverse("email_history_list"),
            {"q": "find-this@example.test"},
            HTTP_HOST="history-alpha.localhost",
        )
        self.assertContains(found, "find-this@example.test")
        self.assertNotContains(
            self.client.get(
                reverse("email_history_list"),
                {"q": "other@example.test"},
                HTTP_HOST="history-alpha.localhost",
            ),
            "find-this@example.test",
        )

        mentor = get_user_model().objects.create_user(
            username="history-alpha-mentor",
            email="mentor@example.test",
        )
        UserOrganizationMembership.objects.create(
            user=mentor,
            organization=self.alpha,
            role="Mentor",
            is_active=True,
        )
        self.client.force_login(mentor)
        denied = self.client.get(
            reverse("email_history_list"), HTTP_HOST="history-alpha.localhost"
        )
        self.assertNotEqual(denied.status_code, 200)

    def test_cleanup_deletes_only_rows_past_thirty_days_and_is_dry_run_by_default(self):
        old_record = self._send_history_message(self.alpha, "old@example.test")
        recent_record = self._send_history_message(self.alpha, "recent@example.test")
        old_batch_id = old_record.batch_id
        recent_batch_id = recent_record.batch_id
        fixed_now = timezone.now()
        cutoff = fixed_now - timedelta(days=30)
        EmailHistory.objects.filter(pk=old_record.pk).update(
            created_at=cutoff - timedelta(seconds=1)
        )
        EmailHistoryBatch.objects.filter(pk=old_record.batch_id).update(
            created_at=cutoff - timedelta(seconds=1)
        )
        EmailHistory.objects.filter(pk=recent_record.pk).update(created_at=cutoff)
        EmailHistoryBatch.objects.filter(pk=recent_record.batch_id).update(
            created_at=cutoff
        )

        output = StringIO()
        with mock.patch(
            "programs.management.commands.cleanup_email_history.timezone.now",
            return_value=fixed_now,
        ):
            call_command("cleanup_email_history", stdout=output)
        self.assertIn("Dry run", output.getvalue())
        self.assertTrue(EmailHistory.objects.filter(pk=old_record.pk).exists())
        self.assertTrue(EmailHistoryBatch.objects.filter(pk=old_batch_id).exists())

        with mock.patch(
            "programs.management.commands.cleanup_email_history.timezone.now",
            return_value=fixed_now,
        ):
            call_command("cleanup_email_history", "--execute", stdout=StringIO())
        self.assertFalse(EmailHistory.objects.filter(pk=old_record.pk).exists())
        self.assertTrue(EmailHistory.objects.filter(pk=recent_record.pk).exists())
        self.assertFalse(EmailHistoryBatch.objects.filter(pk=old_batch_id).exists())
        self.assertTrue(EmailHistoryBatch.objects.filter(pk=recent_batch_id).exists())

    def test_audit_entries_do_not_include_message_content_or_addresses(self):
        record = self._send_history_message(self.alpha, "private-address@example.test")
        self.client.force_login(self.alpha_lead)
        self.client.get(
            reverse("email_history_detail", args=[record.pk]),
            HTTP_HOST="history-alpha.localhost",
        )

        entry = AuditLog.objects.get(
            event=AuditEvent.SENSITIVE_DATA_VIEW,
            resource_type="EmailHistory",
            resource_id=str(record.pk),
        )
        audit_text = json.dumps(
            {"before": entry.before, "after": entry.after, "notes": entry.notes}
        )
        self.assertNotIn("private-address@example.test", audit_text)
        self.assertNotIn("The personalized message body", audit_text)
