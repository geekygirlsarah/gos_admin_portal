from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

from django.db import close_old_connections, connection, connections
from django.test import TransactionTestCase
from django.utils import timezone

from applications.models import Application, ApplicationReminderDelivery
from applications.reminders import _claim_plan
from organizations.models import Organization


class ApplicationReminderClaimConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.organization = Organization.objects.create(
            name="Reminder Concurrency",
            slug="reminder-concurrency",
            subdomain="reminder-concurrency",
        )
        self.now = timezone.now()
        self.application = Application.objects.create(
            organization=self.organization,
            applicant_type=Application.Type.PARENT,
            status=Application.Status.DRAFT,
            email="parent@example.test",
            email_verified_at=self.now,
            applicant_progress_at=self.now - timedelta(hours=48),
        )

    def _claim(self, barrier=None):
        close_old_connections()
        try:
            if barrier is not None:
                barrier.wait(timeout=10)
            return _claim_plan(
                self.organization,
                self.application.pk,
                self.now,
            )
        finally:
            connections.close_all()

    def test_only_one_worker_claims_a_due_reminder(self):
        if connection.features.has_select_for_update:
            barrier = Barrier(2)
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(lambda _: self._claim(barrier), range(2)))
        else:
            results = [self._claim(), self._claim()]

        claims = [result for result in results if result is not None]
        self.assertEqual(len(claims), 1)
        self.assertEqual(
            ApplicationReminderDelivery.objects.filter(
                application=self.application,
                status=ApplicationReminderDelivery.Status.PENDING,
            ).count(),
            1,
        )
