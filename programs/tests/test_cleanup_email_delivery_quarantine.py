import json
from datetime import timedelta
from io import StringIO
from unittest import mock

from cryptography.fernet import Fernet
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from organizations.models import Organization
from programs.models import (
    EmailDeliveryEvent,
    EmailDeliveryQuarantine,
    TrustedOutgoingEmailRecipient,
)
from programs.utils.tosend_webhooks import _email_fingerprint

_FILE_ENCRYPTION_KEY = Fernet.generate_key().decode()


@override_settings(FILE_ENCRYPTION_KEY=_FILE_ENCRYPTION_KEY)
class CleanupEmailDeliveryQuarantineTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.get(slug="gos")
        self.now = timezone.now()
        self.old = self._quarantine("a" * 64, self.now - timedelta(days=30, seconds=1))
        self.at_cutoff = self._quarantine("b" * 64, self.now - timedelta(days=30))
        self.recent = self._quarantine("c" * 64, self.now - timedelta(days=29))
        self.historical_event = EmailDeliveryEvent.objects.create(
            organization=self.organization,
            recipient_email="historical@example.test",
            reason="Historical event remains intact",
            is_verified=False,
        )
        recipient = "verified@example.test"
        message_id = "retained-verified-event"
        recipient_fingerprint = _email_fingerprint(recipient)
        trusted_mapping = TrustedOutgoingEmailRecipient.objects.create(
            organization=self.organization,
            provider_message_id=message_id,
            recipient_email=recipient,
            recipient_fingerprint=recipient_fingerprint,
        )
        self.verified_event = EmailDeliveryEvent.objects.create(
            organization=self.organization,
            recipient_email=recipient,
            recipient_fingerprint=recipient_fingerprint,
            message_id=message_id,
            trusted_mapping=trusted_mapping,
            is_verified=True,
        )

    def _quarantine(self, deduplication_key, received_at):
        return EmailDeliveryQuarantine.objects.create(
            deduplication_key=deduplication_key,
            reason_code=EmailDeliveryQuarantine.REASON_UNMATCHED,
            encrypted_diagnostic=json.dumps(
                {
                    "recipients": ["quarantine@example.test"],
                    "message_id": deduplication_key,
                }
            ),
            received_at=received_at,
        )

    @mock.patch(
        "programs.management.commands.cleanup_email_delivery_quarantine.timezone.now"
    )
    def test_default_dry_run_and_explicit_cutoff_cleanup_preserve_other_data(self, now):
        now.return_value = self.now
        dry_run_output = StringIO()

        call_command("cleanup_email_delivery_quarantine", stdout=dry_run_output)

        self.assertIn(
            "2 quarantined email delivery event(s)", dry_run_output.getvalue()
        )
        self.assertNotIn("quarantine@example.test", dry_run_output.getvalue())
        self.assertEqual(
            EmailDeliveryQuarantine.objects.filter(
                pk__in=[self.old.pk, self.at_cutoff.pk, self.recent.pk]
            ).count(),
            3,
        )
        self.assertTrue(
            EmailDeliveryEvent.objects.filter(pk=self.historical_event.pk).exists()
        )
        self.assertTrue(
            EmailDeliveryEvent.objects.filter(pk=self.verified_event.pk).exists()
        )

        execute_output = StringIO()
        call_command(
            "cleanup_email_delivery_quarantine",
            "--execute",
            stdout=execute_output,
        )

        self.assertIn(
            "Deleted 2 quarantined email delivery event(s)", execute_output.getvalue()
        )
        self.assertFalse(
            EmailDeliveryQuarantine.objects.filter(pk=self.old.pk).exists()
        )
        self.assertFalse(
            EmailDeliveryQuarantine.objects.filter(pk=self.at_cutoff.pk).exists()
        )
        self.assertTrue(
            EmailDeliveryQuarantine.objects.filter(pk=self.recent.pk).exists()
        )
        self.assertTrue(
            EmailDeliveryEvent.objects.filter(pk=self.historical_event.pk).exists()
        )
        self.assertTrue(
            EmailDeliveryEvent.objects.filter(pk=self.verified_event.pk).exists()
        )
