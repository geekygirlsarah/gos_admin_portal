import hashlib
import hmac
import json
import time

from cryptography.fernet import Fernet
from django.core import mail
from django.db import models
from django.db.models.functions import Cast
from django.test import TestCase, override_settings
from django.urls import reverse

from applications.models import Application
from audit.models import AuditLog
from programs.models import (
    Adult,
    EmailDeliveryEvent,
    EmailDeliveryQuarantine,
    Program,
    Student,
)

_FILE_ENCRYPTION_KEY = Fernet.generate_key().decode()


@override_settings(FILE_ENCRYPTION_KEY=_FILE_ENCRYPTION_KEY)
class ToSendWebhookTests(TestCase):
    def setUp(self):
        mail.outbox = []
        self.secret = "test_webhook_secret_key_12345"  # nosec B105

        self.student = Student.objects.create(
            legal_first_name="Jane",
            last_name="Doe",
            personal_email="jane.doe@example.com",
        )
        self.adult = Adult.objects.create(
            legal_first_name="John",
            last_name="Doe",
            personal_email="john.parent@example.com",
            is_parent=True,
        )
        self.program = Program.objects.create(name="FRC 2026-2027", active=True)
        self.application = Application.objects.create(
            program=self.program,
            email="applicant.test@example.com",
            status=Application.Status.SUBMITTED,
        )

    def _sign_payload(
        self, body_bytes: bytes, secret: str = None, timestamp: int = None
    ) -> dict:
        if secret is None:
            secret = self.secret
        ts = int(time.time()) if timestamp is None else timestamp
        signature = hmac.new(
            secret.encode("utf-8"),
            f"{ts}.".encode("utf-8") + body_bytes,
            hashlib.sha256,
        ).hexdigest()
        return {
            "HTTP_X_TOSEND_SIGNATURE": f"t={ts},v1={signature}",
            "HTTP_X_TOSEND_TIMESTAMP": str(ts),
        }

    @override_settings(
        TOSEND_WEBHOOK_SECRET="test_webhook_secret_key_12345"
    )  # nosec B106
    def test_unattributed_webhook_does_not_create_event_audit_or_alert(self):
        payload = {
            "event": "bounced",
            "email": "jane.doe@example.com",
            "bounce_type": "hard",
            "reason": "550 5.1.1 User unknown",
            "message_id": "unattributed_message",
        }
        body = json.dumps(payload).encode("utf-8")
        response = self.client.post(
            reverse("tosend_webhook"),
            data=body,
            content_type="application/json",
            **self._sign_payload(body),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(EmailDeliveryEvent.objects.count(), 0)
        self.assertFalse(
            AuditLog.objects.filter(resource_type="EmailDeliveryEvent").exists()
        )
        self.assertEqual(mail.outbox, [])
        quarantine = EmailDeliveryQuarantine.objects.get()
        self.assertEqual(
            quarantine.reason_code, EmailDeliveryQuarantine.REASON_UNMATCHED
        )
        diagnostic = json.loads(quarantine.encrypted_diagnostic)
        self.assertEqual(diagnostic["recipients"], ["jane.doe@example.com"])
        self.assertEqual(diagnostic["message_id"], "unattributed_message")
        self.assertEqual(diagnostic["reason"], "550 5.1.1 User unknown")
        encrypted_value = (
            EmailDeliveryQuarantine.objects.filter(pk=quarantine.pk)
            .annotate(
                stored_value=Cast(
                    "encrypted_diagnostic", output_field=models.TextField()
                )
            )
            .values_list("stored_value", flat=True)
            .get()
        )
        self.assertNotIn("jane.doe@example.com", encrypted_value)
        self.assertNotIn("550 5.1.1", encrypted_value)

    @override_settings(
        TOSEND_WEBHOOK_SECRET="test_webhook_secret_key_12345"
    )  # nosec B106
    def test_provider_sha256_signature_header_is_accepted(self):
        payload = {
            "type": "bounced",
            "data": {
                "email": "jane.doe@example.com",
                "message_id": "provider_signature_message",
                "bounce_type": "Permanent",
                "is_hard_bounce": True,
                "reason": "User unknown",
            },
            "created_at": "2026-04-18T10:30:00.000Z",
        }
        body = json.dumps(payload).encode("utf-8")
        signature = hmac.new(
            self.secret.encode("utf-8"), body, hashlib.sha256
        ).hexdigest()
        response = self.client.post(
            reverse("tosend_webhook"),
            data=body,
            content_type="application/json",
            HTTP_X_TOSEND_SIGNATURE=f"sha256={signature}",
            HTTP_X_TOSEND_TIMESTAMP="2026-04-18T10:30:00.000Z",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(EmailDeliveryQuarantine.objects.count(), 1)

    @override_settings(
        TOSEND_WEBHOOK_SECRET="test_webhook_secret_key_12345"
    )  # nosec B106
    def test_webhook_hard_bounce_creates_event_and_notifies_lead(self):
        payload = {
            "event": "bounced",
            "email": "jane.doe@example.com",
            "bounce_type": "hard",
            "is_hard_bounce": True,
            "reason": "550 5.1.1 User unknown",
            "message_id": "msg_bounce_999",
            "timestamp": "2026-10-23T10:00:00Z",
        }
        body = json.dumps(payload).encode("utf-8")
        headers = self._sign_payload(body)

        url = reverse("tosend_webhook")
        response = self.client.post(
            url,
            data=body,
            content_type="application/json",
            **headers,
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data.get("status"), "ok")
        self.assertEqual(data.get("processed"), 1)

        self.assertEqual(EmailDeliveryEvent.objects.count(), 0)
        quarantine = EmailDeliveryQuarantine.objects.get()
        self.assertEqual(
            quarantine.reason_code, EmailDeliveryQuarantine.REASON_UNMATCHED
        )
        diagnostic = json.loads(quarantine.encrypted_diagnostic)
        self.assertEqual(diagnostic["event_type"], EmailDeliveryEvent.EVENT_BOUNCED)
        self.assertEqual(diagnostic["bounce_type"], "hard")
        self.assertEqual(diagnostic["recipients"], ["jane.doe@example.com"])
        self.assertEqual(mail.outbox, [])
        self.assertFalse(
            AuditLog.objects.filter(resource_type="EmailDeliveryEvent").exists()
        )

    @override_settings(
        TOSEND_WEBHOOK_SECRET="test_webhook_secret_key_12345"
    )  # nosec B106
    def test_webhook_soft_bounce_creates_event_without_immediate_email(self):
        payload = {
            "event": "bounced",
            "recipient": "john.parent@example.com",
            "bounce_type": "soft",
            "is_hard_bounce": False,
            "reason": "421 4.4.1 Mailbox full",
            "message_id": "msg_soft_111",
        }
        body = json.dumps(payload).encode("utf-8")
        headers = self._sign_payload(body)

        url = reverse("tosend_webhook")
        response = self.client.post(
            url,
            data=body,
            content_type="application/json",
            **headers,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(EmailDeliveryEvent.objects.count(), 0)
        quarantine = EmailDeliveryQuarantine.objects.get()
        diagnostic = json.loads(quarantine.encrypted_diagnostic)
        self.assertEqual(diagnostic["bounce_type"], "soft")
        self.assertEqual(diagnostic["recipients"], ["john.parent@example.com"])
        self.assertEqual(mail.outbox, [])

    @override_settings(
        TOSEND_WEBHOOK_SECRET="test_webhook_secret_key_12345"
    )  # nosec B106
    def test_webhook_complaint_creates_event_and_notifies_lead(self):
        payload = {
            "event": "complaint",
            "email": "applicant.test@example.com",
            "reason": "Spam complaint reported by user",
            "message_id": "msg_complaint_222",
        }
        body = json.dumps(payload).encode("utf-8")
        headers = self._sign_payload(body)

        url = reverse("tosend_webhook")
        response = self.client.post(
            url,
            data=body,
            content_type="application/json",
            **headers,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(EmailDeliveryEvent.objects.count(), 0)
        quarantine = EmailDeliveryQuarantine.objects.get()
        diagnostic = json.loads(quarantine.encrypted_diagnostic)
        self.assertEqual(diagnostic["event_type"], EmailDeliveryEvent.EVENT_COMPLAINT)
        self.assertEqual(diagnostic["recipients"], ["applicant.test@example.com"])
        self.assertEqual(mail.outbox, [])

    @override_settings(
        TOSEND_WEBHOOK_SECRET="test_webhook_secret_key_12345"
    )  # nosec B106
    def test_webhook_invalid_signature_rejected(self):
        payload = {"event": "bounced", "email": "fake@example.com"}
        body = json.dumps(payload).encode("utf-8")
        headers = {"HTTP_X_TOSEND_SIGNATURE": "t=12345,v1=invalidsignature"}

        url = reverse("tosend_webhook")
        response = self.client.post(
            url,
            data=body,
            content_type="application/json",
            **headers,
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(EmailDeliveryEvent.objects.count(), 0)
        self.assertEqual(EmailDeliveryQuarantine.objects.count(), 0)

    @override_settings(
        TOSEND_WEBHOOK_SECRET="test_webhook_secret_key_12345"
    )  # nosec B106
    def test_signed_malformed_json_is_quarantined_without_retaining_body(self):
        body = b"{malformed sensitive provider body}"
        response = self.client.post(
            reverse("tosend_webhook"),
            data=body,
            content_type="application/json",
            **self._sign_payload(body),
        )

        self.assertEqual(response.status_code, 200)
        quarantine = EmailDeliveryQuarantine.objects.get()
        self.assertEqual(
            quarantine.reason_code, EmailDeliveryQuarantine.REASON_MALFORMED
        )
        self.assertEqual(
            json.loads(quarantine.encrypted_diagnostic),
            {"error": "malformed_signed_json"},
        )
        self.assertNotIn("sensitive", quarantine.encrypted_diagnostic)

    @override_settings(
        TOSEND_WEBHOOK_SECRET="test_webhook_secret_key_12345"
    )  # nosec B106
    def test_ambiguous_recipient_list_is_quarantined(self):
        payload = {
            "event": "bounced",
            "to": [
                {"email": "first@example.com"},
                {"email": "second@example.com"},
            ],
            "message_id": "ambiguous_message",
        }
        body = json.dumps(payload).encode("utf-8")
        response = self.client.post(
            reverse("tosend_webhook"),
            data=body,
            content_type="application/json",
            **self._sign_payload(body),
        )

        self.assertEqual(response.status_code, 200)
        quarantine = EmailDeliveryQuarantine.objects.get()
        self.assertEqual(
            quarantine.reason_code, EmailDeliveryQuarantine.REASON_AMBIGUOUS
        )
        self.assertEqual(
            json.loads(quarantine.encrypted_diagnostic)["recipients"],
            ["first@example.com", "second@example.com"],
        )

    @override_settings(
        TOSEND_WEBHOOK_SECRET="test_webhook_secret_key_12345"
    )  # nosec B106
    def test_webhook_batch_event_payload(self):
        payload = [
            {
                "event": "bounced",
                "email": "jane.doe@example.com",
                "bounce_type": "hard",
                "message_id": "msg_batch_1",
            },
            {
                "event": "complaint",
                "email": "john.parent@example.com",
                "message_id": "msg_batch_2",
            },
        ]
        body = json.dumps(payload).encode("utf-8")
        headers = self._sign_payload(body)

        url = reverse("tosend_webhook")
        response = self.client.post(
            url,
            data=body,
            content_type="application/json",
            **headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json().get("processed"), 2)
        self.assertEqual(EmailDeliveryEvent.objects.count(), 0)
        self.assertEqual(EmailDeliveryQuarantine.objects.count(), 2)

    @override_settings(
        TOSEND_WEBHOOK_SECRET="test_webhook_secret_key_12345"
    )  # nosec B106
    def test_webhook_idempotent_duplicate_events(self):
        payload = {
            "event": "bounced",
            "email": "jane.doe@example.com",
            "bounce_type": "hard",
            "message_id": "msg_idemp_1",
        }
        body = json.dumps(payload).encode("utf-8")
        headers = self._sign_payload(body)
        url = reverse("tosend_webhook")

        res1 = self.client.post(
            url, data=body, content_type="application/json", **headers
        )
        self.assertEqual(res1.status_code, 200)
        self.assertEqual(EmailDeliveryQuarantine.objects.count(), 1)

        # Replayed signed events are idempotent.
        res2 = self.client.post(
            url, data=body, content_type="application/json", **headers
        )
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(EmailDeliveryQuarantine.objects.count(), 1)
