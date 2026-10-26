import hashlib
import hmac
import json
import time

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from applications.models import Application
from audit.events import AuditEvent
from audit.models import AuditLog
from programs.models import Adult, EmailDeliveryEvent, Program, Student


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

        event = EmailDeliveryEvent.objects.get(message_id="msg_bounce_999")
        self.assertEqual(event.recipient_email, "jane.doe@example.com")
        self.assertEqual(event.event_type, EmailDeliveryEvent.EVENT_BOUNCED)
        self.assertEqual(event.bounce_type, EmailDeliveryEvent.BOUNCE_HARD)
        self.assertTrue(event.is_hard_bounce)
        self.assertTrue(event.is_critical)
        self.assertEqual(event.student, self.student)
        self.assertEqual(event.reason, "550 5.1.1 User unknown")

        # Verify AuditLog created
        audit_log = AuditLog.objects.filter(
            event=AuditEvent.EMAIL_DELIVERY_FAILED
        ).first()
        self.assertIsNotNone(audit_log)
        self.assertEqual(audit_log.resource_type, "EmailDeliveryEvent")
        self.assertIn("jane.doe@example.com", audit_log.notes)

        # Verify Lead Mentor notification email was sent
        self.assertGreaterEqual(len(mail.outbox), 1)
        lead_email = mail.outbox[0]
        self.assertIn("Delivery Issue", lead_email.subject)
        self.assertIn("jane.doe@example.com", lead_email.body)
        self.assertIn("Jane Doe", lead_email.body)

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
        event = EmailDeliveryEvent.objects.get(message_id="msg_soft_111")
        self.assertEqual(event.recipient_email, "john.parent@example.com")
        self.assertEqual(event.bounce_type, EmailDeliveryEvent.BOUNCE_SOFT)
        self.assertFalse(event.is_hard_bounce)
        self.assertFalse(event.is_critical)
        self.assertEqual(event.adult, self.adult)

        # Soft bounce does not trigger immediate lead mentor alert email
        self.assertEqual(len(mail.outbox), 0)

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
        event = EmailDeliveryEvent.objects.get(message_id="msg_complaint_222")
        self.assertEqual(event.recipient_email, "applicant.test@example.com")
        self.assertEqual(event.event_type, EmailDeliveryEvent.EVENT_COMPLAINT)
        self.assertTrue(event.is_critical)
        self.assertEqual(event.application, self.application)

        # Spam complaint alerts lead mentors immediately
        self.assertGreaterEqual(len(mail.outbox), 1)
        self.assertIn("Spam Complaint", mail.outbox[0].subject)

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
        self.assertEqual(EmailDeliveryEvent.objects.count(), 2)

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
        self.assertEqual(
            EmailDeliveryEvent.objects.filter(message_id="msg_idemp_1").count(), 1
        )

        # Second delivery with identical message_id does not duplicate or raise error
        res2 = self.client.post(
            url, data=body, content_type="application/json", **headers
        )
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(
            EmailDeliveryEvent.objects.filter(message_id="msg_idemp_1").count(), 1
        )
