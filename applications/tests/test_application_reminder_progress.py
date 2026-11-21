from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from applications.models import Application, ApplicationReminderDelivery
from applications.views.utils import _save_step_data
from organizations.models import Organization
from programs.models import Program


class ApplicationReminderProgressTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(
            name="Reminder Tests", slug="reminder-tests", subdomain="reminder-tests"
        )
        self.other_organization = Organization.objects.create(
            name="Other Reminder Tests",
            slug="other-reminder-tests",
            subdomain="other-reminder-tests",
        )
        self.program = Program.objects.create(
            organization=self.organization, name="Reminder Program"
        )
        self.application = Application.objects.create(
            organization=self.organization,
            program=self.program,
            applicant_type=Application.Type.PARENT,
            email="parent@example.test",
        )

    def test_new_applications_initialize_applicant_progress(self):
        self.assertIsNotNone(self.application.applicant_progress_at)

    def test_successful_locked_step_write_advances_applicant_progress(self):
        old_progress = timezone.now() - timedelta(days=3)
        Application.objects.filter(pk=self.application.pk).update(
            applicant_progress_at=old_progress
        )
        self.application.refresh_from_db()

        result = _save_step_data(
            self.application,
            "step5-student",
            {"legal_first_name": "Sam"},
            next_step=6,
        )

        self.assertIsNone(result)
        self.application.refresh_from_db()
        self.assertGreater(self.application.applicant_progress_at, old_progress)

    def test_otp_activity_does_not_advance_applicant_progress(self):
        old_progress = self.application.applicant_progress_at

        self.application.issue_otp()

        self.application.refresh_from_db()
        self.assertEqual(self.application.applicant_progress_at, old_progress)

    def test_reminder_ledger_rejects_cross_organization_application(self):
        delivery = ApplicationReminderDelivery(
            application=self.application,
            organization=self.other_organization,
            phase=ApplicationReminderDelivery.Phase.DRAFT,
            reminder_type=ApplicationReminderDelivery.ReminderType.INACTIVITY,
        )

        with self.assertRaises(ValidationError):
            delivery.full_clean()
        with self.assertRaises(ValidationError):
            delivery.save()

    def test_reminder_ledger_is_unique_per_application_phase_and_type(self):
        ApplicationReminderDelivery.objects.create(
            application=self.application,
            organization=self.organization,
            phase=ApplicationReminderDelivery.Phase.DRAFT,
            reminder_type=ApplicationReminderDelivery.ReminderType.INACTIVITY,
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ApplicationReminderDelivery.objects.create(
                    application=self.application,
                    organization=self.organization,
                    phase=ApplicationReminderDelivery.Phase.DRAFT,
                    reminder_type=ApplicationReminderDelivery.ReminderType.INACTIVITY,
                )
