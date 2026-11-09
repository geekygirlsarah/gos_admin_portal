import hashlib
import hmac
import json
from unittest import mock

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.core import mail
from django.db import models, transaction
from django.db.models.functions import Cast
from django.template.loader import render_to_string
from django.test import TransactionTestCase, override_settings
from django.urls import reverse

from GoSAdminPortal.mail_backends import RedirectEmailBackend, ToSendEmailBackend
from organizations.models import Organization, UserOrganizationMembership
from programs.models import (
    EmailDeliveryEvent,
    EmailDeliveryQuarantine,
    Student,
    TrustedOutgoingEmailRecipient,
)
from programs.utils.notifications import _mark_delivery_tracking
from programs.utils.tosend_webhooks import record_trusted_outgoing_message

_FILE_ENCRYPTION_KEY = Fernet.generate_key().decode()
_WEBHOOK_SECRET = "tracking-webhook-test-secret"  # nosec B105


@override_settings(
    ALLOWED_HOSTS=[".localhost", "testserver"],
    FILE_ENCRYPTION_KEY=_FILE_ENCRYPTION_KEY,
    MOMENTUM_ROOT_DOMAIN="localhost",
    TOSEND_WEBHOOK_SECRET=_WEBHOOK_SECRET,
)
class EmailDeliveryTrackingTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        mail.outbox = []
        self.alpha = Organization.objects.create(
            name="Tracking Alpha", slug="tracking-alpha", subdomain="tracking-alpha"
        )
        self.beta = Organization.objects.create(
            name="Tracking Beta", slug="tracking-beta", subdomain="tracking-beta"
        )
        self.alpha_student = Student.objects.create(
            organization=self.alpha,
            legal_first_name="Avery",
            last_name="Alpha",
            personal_email="shared@example.test",
        )
        self.beta_student = Student.objects.create(
            organization=self.beta,
            legal_first_name="Blair",
            last_name="Beta",
            personal_email="shared@example.test",
        )
        self.alpha_lead = get_user_model().objects.create_user(
            username="tracking-alpha-lead",
            email="alpha-lead@example.test",
            password="password123",
        )  # nosec B106
        UserOrganizationMembership.objects.create(
            user=self.alpha_lead,
            organization=self.alpha,
            role="LeadMentor",
            is_active=True,
        )
        self.beta_lead = get_user_model().objects.create_user(
            username="tracking-beta-lead",
            email="beta-lead@example.test",
            password="password123",
        )  # nosec B106
        UserOrganizationMembership.objects.create(
            user=self.beta_lead,
            organization=self.beta,
            role="LeadMentor",
            is_active=True,
        )
        self.backend = ToSendEmailBackend(api_key="test-api-key")

    def _message(self, organization, recipient, *, track=True, cc=None, bcc=None):
        from django.core.mail import EmailMultiAlternatives

        message = EmailMultiAlternatives(
            subject="A generic test notice",
            body="A generic body",
            from_email="notifications@example.test",
            to=[recipient],
            cc=cc or [],
            bcc=bcc or [],
        )
        _mark_delivery_tracking(message, organization, enabled=track)
        return message

    def _provider_response(self, status_code, body):
        response = mock.Mock()
        response.status_code = status_code
        response.json.return_value = body
        response.headers = {}
        return response

    @mock.patch(
        "django.core.mail.message.EmailMessage.send", autospec=True, return_value=1
    )
    def test_templated_notifier_attaches_internal_organization_marker(self, mock_send):
        from programs.utils.notifications import send_templated_notification

        send_templated_notification(
            "A generic test notification",
            "programs/emails/delivery_issue_alert.html",
            {"event_description": "A generic delivery event"},
            ["recipient@example.test"],
            organization=self.alpha,
        )
        tracked_message = mock_send.call_args.args[0]
        self.assertTrue(tracked_message._momentum_delivery_tracking)
        self.assertEqual(tracked_message._momentum_organization_id, self.alpha.pk)

        send_templated_notification(
            "An untracked test notification",
            "programs/emails/delivery_issue_alert.html",
            {"event_description": "A generic delivery event"},
            ["test-destination@example.test"],
            organization=self.alpha,
            track_delivery=False,
        )
        untracked_message = mock_send.call_args.args[0]
        self.assertFalse(untracked_message._momentum_delivery_tracking)
        self.assertEqual(untracked_message._momentum_organization_id, self.alpha.pk)

    def _post_webhook(
        self, message_id, recipient, event_type="bounced", reason="Rejected"
    ):
        payload = {
            "type": event_type,
            "data": {
                "email": recipient,
                "message_id": message_id,
                "reason": reason,
                "bounce_type": "Permanent",
                "is_hard_bounce": True,
            },
            "created_at": "2026-04-18T10:30:00.000Z",
        }
        body = json.dumps(payload).encode("utf-8")
        signature = hmac.new(_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
        return self.client.post(
            reverse("api_tosend_webhook"),
            data=body,
            content_type="application/json",
            HTTP_HOST="localhost",
            HTTP_X_TOSEND_SIGNATURE=f"sha256={signature}",
            HTTP_X_TOSEND_TIMESTAMP="2026-04-18T10:30:00.000Z",
        )

    @mock.patch("GoSAdminPortal.mail_backends.requests.post")
    def test_single_success_maps_all_actual_recipients_encrypted(self, mock_post):
        mock_post.return_value = self._provider_response(
            200, {"message_id": "single-message-1"}
        )
        message = self._message(
            self.alpha,
            "Avery <avery@example.test>",
        )

        self.assertEqual(self.backend.send_messages([message]), 1)

        mappings = TrustedOutgoingEmailRecipient.objects.filter(
            provider_message_id="single-message-1"
        )
        self.assertEqual(mappings.count(), 1)
        self.assertEqual(
            set(mappings.values_list("recipient_email", flat=True)),
            {"avery@example.test"},
        )
        stored_value = (
            mappings.annotate(
                stored_value=Cast("recipient_email", output_field=models.TextField())
            )
            .values_list("stored_value", flat=True)
            .get()
        )
        self.assertNotIn("avery@example.test", stored_value)

    @mock.patch("GoSAdminPortal.mail_backends.requests.post")
    def test_multi_recipient_single_message_remains_unattributed(self, mock_post):
        mock_post.return_value = self._provider_response(
            200, {"message_id": "multi-recipient-message"}
        )
        message = self._message(
            self.alpha,
            "one@example.test",
            cc=["two@example.test"],
            bcc=["three@example.test"],
        )

        self.assertEqual(self.backend.send_messages([message]), 1)

        self.assertFalse(
            TrustedOutgoingEmailRecipient.objects.filter(
                provider_message_id="multi-recipient-message"
            ).exists()
        )

    @mock.patch("GoSAdminPortal.mail_backends.requests.post")
    def test_partial_batch_maps_only_successful_ordered_results(self, mock_post):
        mock_post.return_value = self._provider_response(
            207,
            {
                "results": [
                    {"status": "success", "message_id": "batch-message-1"},
                    {"status": "error", "status_code": 422},
                    {"status": "success", "message_id": "batch-message-3"},
                ]
            },
        )
        messages = [
            self._message(self.alpha, f"person{index}@example.test")
            for index in range(1, 4)
        ]

        self.assertEqual(self.backend.send_messages(messages), 2)

        self.assertEqual(
            TrustedOutgoingEmailRecipient.objects.get(
                provider_message_id="batch-message-1"
            ).recipient_email,
            "person1@example.test",
        )
        self.assertFalse(
            TrustedOutgoingEmailRecipient.objects.filter(
                recipient_email="person2@example.test"
            ).exists()
        )
        self.assertEqual(
            TrustedOutgoingEmailRecipient.objects.get(
                provider_message_id="batch-message-3"
            ).recipient_email,
            "person3@example.test",
        )

        mock_post.side_effect = [
            self._provider_response(
                200,
                {
                    "results": [
                        {"status": "success", "message_id": f"complete-batch-{index}"}
                        for index in range(100)
                    ]
                },
            ),
            self._provider_response(
                200,
                {"results": [{"status": "success", "message_id": "single-batch-id"}]},
            ),
        ]
        complete_batch = [
            self._message(self.alpha, f"complete{index}@example.test")
            for index in range(101)
        ]
        self.assertEqual(self.backend.send_messages(complete_batch), 101)
        self.assertFalse(
            TrustedOutgoingEmailRecipient.objects.filter(
                provider_message_id__startswith="complete-batch-"
            ).exists()
        )

        self.assertEqual(
            TrustedOutgoingEmailRecipient.objects.get(
                provider_message_id="single-batch-id"
            ).recipient_email,
            "complete100@example.test",
        )

    @mock.patch("GoSAdminPortal.mail_backends.requests.post")
    def test_duplicate_or_missing_provider_ids_remain_unattributed(self, mock_post):
        mock_post.return_value = self._provider_response(
            200,
            {
                "results": [
                    {"status": "success", "message_id": "duplicate-id"},
                    {"status": "success", "message_id": "duplicate-id"},
                ]
            },
        )
        batch = [
            self._message(self.alpha, "one@example.test"),
            self._message(self.alpha, "two@example.test"),
        ]
        self.assertEqual(self.backend.send_messages(batch), 2)
        self.assertFalse(
            TrustedOutgoingEmailRecipient.objects.filter(
                provider_message_id="duplicate-id"
            ).exists()
        )

        mock_post.return_value = self._provider_response(200, {"status": "success"})
        missing_id = self._message(self.alpha, "missing-id@example.test")
        self.assertEqual(self.backend.send_messages([missing_id]), 1)
        self.assertFalse(
            TrustedOutgoingEmailRecipient.objects.filter(
                recipient_email="missing-id@example.test"
            ).exists()
        )

    @mock.patch("GoSAdminPortal.mail_backends.requests.post")
    def test_headers_and_redirected_recipients_never_create_mapping(self, mock_post):
        mock_post.return_value = self._provider_response(
            200, {"message_id": "untrusted-header-id"}
        )
        header_only = self._message(self.alpha, "header@example.test", track=False)
        header_only.extra_headers["X-Organization-ID"] = str(self.alpha.pk)
        self.assertEqual(self.backend.send_messages([header_only]), 1)

        redirected = self._message(self.alpha, "private@example.test")
        redirect_backend = RedirectEmailBackend(
            redirect_to="sink@example.test",
            backend="GoSAdminPortal.mail_backends.ToSendEmailBackend",
            backend_options={"api_key": "test-api-key"},
        )
        mock_post.return_value = self._provider_response(
            200, {"message_id": "redirected-id"}
        )
        self.assertEqual(redirect_backend.send_messages([redirected]), 1)
        self.assertFalse(TrustedOutgoingEmailRecipient.objects.exists())

    def test_webhook_correlates_exact_message_and_recipient_and_alerts_local_leads(
        self,
    ):
        recipient = "shared@example.test"
        record_trusted_outgoing_message(self.alpha.pk, "mapped-message", [recipient])
        self.assertEqual(
            TrustedOutgoingEmailRecipient.objects.filter(
                provider_message_id="mapped-message"
            ).count(),
            1,
        )

        with mock.patch(
            "programs.utils.notifications.send_templated_notification"
        ) as send_alert:
            response = self._post_webhook("mapped-message", recipient)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["processed"], 1)
            event = EmailDeliveryEvent.objects.get()
            self.assertTrue(event.is_verified)
            self.assertEqual(event.organization_id, self.alpha.pk)
            self.assertEqual(event.student_id, self.alpha_student.pk)
            self.assertEqual(
                EmailDeliveryEvent.objects.verified_for_organization(
                    self.alpha
                ).count(),
                1,
            )
            self.assertEqual(send_alert.call_count, 1)
            self.assertEqual(send_alert.call_args.args[3], ["alpha-lead@example.test"])
            self.assertFalse(send_alert.call_args.kwargs["track_delivery"])
            alert_text = json.dumps(send_alert.call_args.args[2])
            self.assertNotIn(recipient, alert_text)
            self.assertNotIn("Avery Alpha", alert_text)
            rendered_alert = render_to_string(
                "programs/emails/delivery_issue_alert.html",
                {
                    **send_alert.call_args.args[2],
                    "event": event,
                    "person_label": "Avery Alpha",
                    "organization": self.alpha,
                    "org_email_settings": {"organization_name": "Tracking Alpha"},
                },
            )
            self.assertNotIn(recipient, rendered_alert)
            self.assertNotIn("Avery Alpha", rendered_alert)
            self.assertNotIn("Rejected", rendered_alert)
            self.assertNotIn("mapped-message", rendered_alert)

            replay = self._post_webhook("mapped-message", recipient)
            self.assertEqual(replay.status_code, 200)
            self.assertEqual(EmailDeliveryEvent.objects.count(), 1)
            self.assertEqual(send_alert.call_count, 1)

    def test_early_webhook_is_reconciled_after_mapping_commit(self):
        message_id = "early-message"
        recipient = "early@example.test"
        early = self._post_webhook(message_id, recipient, event_type="complaint")
        self.assertEqual(early.status_code, 200)
        quarantine = EmailDeliveryQuarantine.objects.get()
        self.assertIsNone(quarantine.reconciled_at)
        self.assertFalse(EmailDeliveryEvent.objects.exists())

        with mock.patch("programs.utils.notifications.send_templated_notification"):
            record_trusted_outgoing_message(self.alpha.pk, message_id, [recipient])

        quarantine.refresh_from_db()
        self.assertIsNotNone(quarantine.reconciled_at)
        event = EmailDeliveryEvent.objects.get()
        self.assertTrue(event.is_verified)
        self.assertEqual(event.organization_id, self.alpha.pk)

    def test_mismatched_recipient_and_cross_tenant_message_id_are_quarantined(self):
        record_trusted_outgoing_message(
            self.alpha.pk, "recipient-mismatch", ["expected@example.test"]
        )
        response = self._post_webhook("recipient-mismatch", "actual@example.test")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(EmailDeliveryEvent.objects.exists())
        self.assertEqual(
            EmailDeliveryQuarantine.objects.filter(
                reason_code=EmailDeliveryQuarantine.REASON_UNMATCHED
            ).count(),
            1,
        )

        record_trusted_outgoing_message(
            self.alpha.pk, "collision-message", ["collision@example.test"]
        )
        with mock.patch("programs.utils.notifications.send_templated_notification"):
            first_collision_event = self._post_webhook(
                "collision-message", "collision@example.test"
            )
        self.assertEqual(first_collision_event.status_code, 200)
        self.assertTrue(
            EmailDeliveryEvent.objects.verified_for_organization(self.alpha)
            .filter(message_id="collision-message")
            .exists()
        )

        record_trusted_outgoing_message(
            self.beta.pk, "collision-message", ["collision@example.test"]
        )
        self.assertTrue(
            TrustedOutgoingEmailRecipient.objects.filter(
                provider_message_id="collision-message", is_ambiguous=True
            ).exists()
        )
        self.assertFalse(
            EmailDeliveryEvent.objects.verified_for_organization(self.alpha)
            .filter(message_id="collision-message")
            .exists()
        )
        collision = self._post_webhook("collision-message", "collision@example.test")
        self.assertEqual(collision.status_code, 200)
        self.assertTrue(
            EmailDeliveryEvent.objects.filter(message_id="collision-message").exists()
        )
        self.assertTrue(
            EmailDeliveryQuarantine.objects.filter(
                reason_code=EmailDeliveryQuarantine.REASON_AMBIGUOUS
            ).exists()
        )

    def test_mapping_is_idempotent_and_rolled_back_with_send_transaction(self):
        record_trusted_outgoing_message(
            self.alpha.pk, "idempotent-message", ["same@example.test"]
        )
        record_trusted_outgoing_message(
            self.alpha.pk, "idempotent-message", ["same@example.test"]
        )
        self.assertEqual(
            TrustedOutgoingEmailRecipient.objects.filter(
                provider_message_id="idempotent-message"
            ).count(),
            1,
        )

        message = self._message(self.alpha, "rolled-back@example.test")
        backend = ToSendEmailBackend(api_key="test-api-key")
        with mock.patch("GoSAdminPortal.mail_backends.requests.post") as mock_post:
            mock_post.return_value = self._provider_response(
                200, {"message_id": "rolled-back-message"}
            )
            with self.assertRaises(RuntimeError):
                with transaction.atomic():
                    self.assertEqual(backend.send_messages([message]), 1)
                    self.assertFalse(
                        TrustedOutgoingEmailRecipient.objects.filter(
                            provider_message_id="rolled-back-message"
                        ).exists()
                    )
                    raise RuntimeError("rollback test transaction")
        self.assertFalse(
            TrustedOutgoingEmailRecipient.objects.filter(
                provider_message_id="rolled-back-message"
            ).exists()
        )
