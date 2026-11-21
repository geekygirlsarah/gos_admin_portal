from datetime import timedelta
from unittest.mock import patch

from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from applications import reminders
from applications.models import (
    Application,
    ApplicationDocumentSubmission,
    ApplicationReminderDelivery,
)
from applications.reminders import run_application_reminder_batch
from organizations.models import Organization
from organizations.utils import get_organization_url
from programs.models import Program, ProgramDocument


class ApplicationReminderTests(TestCase):
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
            organization=self.organization,
            name="Reminder Program",
            applications_close=timezone.now().date() + timedelta(days=10),
        )
        mail.outbox.clear()

    def make_application(
        self,
        *,
        applicant_type=Application.Type.PARENT,
        status=Application.Status.DRAFT,
        program=None,
        email="applicant@example.test",
        email_verified=True,
        data=None,
        **kwargs,
    ):
        return Application.objects.create(
            organization=self.organization,
            program=program,
            applicant_type=applicant_type,
            status=status,
            email=email,
            email_verified_at=timezone.now() if email_verified else None,
            data=data if data is not None else {},
            **kwargs,
        )

    def set_progress(self, application, value):
        Application.objects.filter(pk=application.pk).update(
            applicant_progress_at=value
        )
        application.refresh_from_db()

    def run_batch(self, *, now=None, dry_run=False, organization=None, limit=200):
        return run_application_reminder_batch(
            organization or self.organization,
            now=now or timezone.now(),
            dry_run=dry_run,
            limit=limit,
        )

    def test_inactivity_uses_progress_time_and_legacy_updated_at_fallback(self):
        now = timezone.now()
        application = self.make_application()
        self.set_progress(application, now - timedelta(hours=47, minutes=59))

        before_due = self.run_batch(now=now, dry_run=True)
        self.assertEqual(before_due["planned"], 0)

        self.set_progress(application, now - timedelta(hours=48))
        due = self.run_batch(now=now, dry_run=True)
        self.assertEqual(due["planned"], 1)

        Application.objects.filter(pk=application.pk).update(
            applicant_progress_at=None,
            updated_at=now - timedelta(hours=48),
        )
        legacy_due = self.run_batch(now=now, dry_run=True)
        self.assertEqual(legacy_due["planned"], 1)

    def test_deadline_is_due_only_in_the_final_two_open_date_days(self):
        now = timezone.now()
        application = self.make_application(program=self.program)
        self.set_progress(application, now - timedelta(hours=1))

        self.program.applications_close = now.date() + timedelta(days=3)
        self.program.save(update_fields=["applications_close", "updated_at"])
        not_due = self.run_batch(now=now, dry_run=True)
        self.assertEqual(not_due["planned"], 0)

        self.program.applications_close = now.date() + timedelta(days=2)
        self.program.save(update_fields=["applications_close", "updated_at"])
        due = self.run_batch(now=now, dry_run=True)
        self.assertEqual(due["planned"], 1)

        self.program.applications_close = now.date()
        self.program.save(update_fields=["applications_close", "updated_at"])
        due_on_closing_date = self.run_batch(now=now, dry_run=True)
        self.assertEqual(due_on_closing_date["planned"], 1)

    def test_dry_run_is_read_only(self):
        now = timezone.now()
        application = self.make_application()
        self.set_progress(application, now - timedelta(hours=48))

        result = self.run_batch(now=now, dry_run=True)

        self.assertEqual(result["planned"], 1)
        self.assertEqual(mail.outbox, [])
        self.assertFalse(
            ApplicationReminderDelivery.objects.filter(application=application).exists()
        )

    def test_management_command_dry_run_is_read_only(self):
        from io import StringIO

        now = timezone.now()
        application = self.make_application()
        self.set_progress(application, now - timedelta(hours=48))
        output = StringIO()

        call_command(
            "send_application_reminders",
            organization=self.organization.slug,
            dry_run=True,
            stdout=output,
        )

        self.assertIn("1 reminder message(s) would be sent", output.getvalue())
        self.assertEqual(mail.outbox, [])
        self.assertFalse(
            ApplicationReminderDelivery.objects.filter(application=application).exists()
        )

    def test_management_command_requires_an_active_organization(self):
        with self.assertRaises(CommandError):
            call_command(
                "send_application_reminders",
                organization="missing-organization",
                dry_run=True,
            )

    def test_overlapping_triggers_are_combined_and_delivered_once(self):
        now = timezone.now()
        application = self.make_application(program=self.program)
        self.set_progress(application, now - timedelta(hours=48))
        self.program.applications_close = now.date() + timedelta(days=1)
        self.program.save(update_fields=["applications_close", "updated_at"])

        first = self.run_batch(now=now)
        second = self.run_batch(now=now)

        self.assertEqual(first["sent"], 1)
        self.assertEqual(second["sent"], 0)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(
            set(
                ApplicationReminderDelivery.objects.filter(
                    application=application
                ).values_list("reminder_type", flat=True)
            ),
            {
                ApplicationReminderDelivery.ReminderType.INACTIVITY,
                ApplicationReminderDelivery.ReminderType.DEADLINE,
            },
        )
        self.assertEqual(
            ApplicationReminderDelivery.objects.filter(
                application=application,
                status=ApplicationReminderDelivery.Status.ACCEPTED,
            ).count(),
            2,
        )
        self.assertIn("48 hours", mail.outbox[0].body)
        self.assertIn("application window", mail.outbox[0].body)

    def test_previously_delivered_trigger_does_not_block_a_later_trigger(self):
        now = timezone.now()
        application = self.make_application(program=self.program)
        self.set_progress(application, now - timedelta(hours=72))
        self.program.applications_close = now.date() + timedelta(days=10)
        self.program.save(update_fields=["applications_close", "updated_at"])

        first = self.run_batch(now=now)
        self.program.applications_close = now.date() + timedelta(days=2)
        self.program.save(update_fields=["applications_close", "updated_at"])
        second = self.run_batch(now=now)

        self.assertEqual(first["sent"], 1)
        self.assertEqual(second["sent"], 1)
        self.assertEqual(len(mail.outbox), 2)
        self.assertNotIn("application window", mail.outbox[0].body)
        self.assertIn("application window", mail.outbox[1].body)
        self.assertEqual(
            ApplicationReminderDelivery.objects.filter(
                application=application,
                status=ApplicationReminderDelivery.Status.ACCEPTED,
            ).count(),
            2,
        )

    def test_student_handoff_reminder_uses_parent_and_existing_token_link(self):
        now = timezone.now()
        application = self.make_application(
            applicant_type=Application.Type.STUDENT,
            status=Application.Status.AWAITING_PARENT,
            email="student@example.test",
            data={"step7_handoff": {"parent_email": "guardian@example.test"}},
        )
        handoff_token = application.issue_handoff_token()
        self.set_progress(application, now - timedelta(hours=48))

        result = self.run_batch(now=now)

        self.assertEqual(result["sent"], 1)
        self.assertEqual(mail.outbox[0].to, ["guardian@example.test"])
        self.assertIn(handoff_token, mail.outbox[0].body)
        self.assertIn(get_organization_url(self.organization), mail.outbox[0].body)
        self.assertNotIn("student@example.test", mail.outbox[0].body)

    def test_awaiting_parent_candidates_require_handoff_token_and_guardian_email(self):
        now = timezone.now()
        missing_token = self.make_application(
            applicant_type=Application.Type.STUDENT,
            status=Application.Status.AWAITING_PARENT,
            email="student-without-token@example.test",
            data={"step7_handoff": {"parent_email": "guardian@example.test"}},
        )
        missing_guardian = self.make_application(
            applicant_type=Application.Type.STUDENT,
            status=Application.Status.AWAITING_PARENT,
            email="student-without-guardian@example.test",
            data={"step7_handoff": {}},
        )
        missing_guardian.issue_handoff_token()
        for application in (missing_token, missing_guardian):
            self.set_progress(application, now - timedelta(hours=48))

        candidate_ids = set(
            reminders._candidate_queryset(self.organization, now).values_list(
                "pk", flat=True
            )
        )

        self.assertNotIn(missing_token.pk, candidate_ids)
        self.assertNotIn(missing_guardian.pk, candidate_ids)

    def test_missing_documents_reminder_targets_guardian_and_protected_route(self):
        now = timezone.now()
        application = self.make_application(
            applicant_type=Application.Type.STUDENT,
            status=Application.Status.SUBMITTED,
            program=self.program,
            email="student@example.test",
            data={"step7_handoff": {"parent_email": "guardian@example.test"}},
        )
        self.set_progress(application, now - timedelta(hours=48))
        ProgramDocument.objects.create(
            program=self.program,
            name="Permission form",
            file="permission-form.pdf",
            is_required=True,
        )

        result = self.run_batch(now=now)

        self.assertEqual(result["sent"], 1)
        self.assertEqual(mail.outbox[0].to, ["guardian@example.test"])
        self.assertIn(get_organization_url(self.organization), mail.outbox[0].body)
        self.assertIn(f"/{application.application_id}/step10/", mail.outbox[0].body)
        self.assertIn("email verification step", mail.outbox[0].body.lower())
        self.assertNotIn("document-handoff-token", mail.outbox[0].body)

    def test_submitted_application_with_complete_documents_is_not_reminded(self):
        now = timezone.now()
        application = self.make_application(
            applicant_type=Application.Type.PARENT,
            status=Application.Status.SUBMITTED,
            program=self.program,
            email="guardian@example.test",
        )
        self.set_progress(application, now - timedelta(hours=72))
        document = ProgramDocument.objects.create(
            program=self.program,
            name="Permission form",
            file="permission-form.pdf",
            is_required=True,
        )
        ApplicationDocumentSubmission.objects.create(
            application=application,
            organization=self.organization,
            document=document,
            file="signed-permission-form.pdf",
        )

        result = self.run_batch(now=now)

        self.assertEqual(result["planned"], 0)
        self.assertEqual(mail.outbox, [])

    def test_unverified_initial_email_is_not_a_reminder_recipient(self):
        now = timezone.now()
        application = self.make_application(email_verified=False)
        self.set_progress(application, now - timedelta(hours=72))

        result = self.run_batch(now=now)

        self.assertEqual(result["sent"], 0)
        self.assertEqual(mail.outbox, [])
        self.assertFalse(
            ApplicationReminderDelivery.objects.filter(application=application).exists()
        )

    def test_malformed_application_data_is_not_reminded(self):
        now = timezone.now()
        application = self.make_application(data=["invalid legacy state"])
        self.set_progress(application, now - timedelta(hours=72))

        result = self.run_batch(now=now, dry_run=True)

        self.assertEqual(result["planned"], 0)
        self.assertEqual(mail.outbox, [])
        self.assertFalse(
            ApplicationReminderDelivery.objects.filter(application=application).exists()
        )

    def test_closed_program_suppresses_all_reminders(self):
        now = timezone.now()
        application = self.make_application(program=self.program)
        self.set_progress(application, now - timedelta(hours=72))
        self.program.applications_close = now.date() - timedelta(days=1)
        self.program.save(update_fields=["applications_close", "updated_at"])

        result = self.run_batch(now=now, dry_run=True)

        self.assertEqual(result["planned"], 0)

    def test_mentor_without_program_gets_only_inactivity_reminder(self):
        now = timezone.now()
        application = self.make_application(
            applicant_type=Application.Type.MENTOR,
            email="mentor@example.test",
        )
        self.set_progress(application, now - timedelta(hours=48))

        result = self.run_batch(now=now)

        self.assertEqual(result["sent"], 1)
        self.assertEqual(mail.outbox[0].to, ["mentor@example.test"])
        self.assertEqual(
            list(
                ApplicationReminderDelivery.objects.filter(
                    application=application
                ).values_list("reminder_type", flat=True)
            ),
            [ApplicationReminderDelivery.ReminderType.INACTIVITY],
        )

    def test_unknown_delivery_is_not_retried(self):
        now = timezone.now()
        application = self.make_application()
        self.set_progress(application, now - timedelta(hours=48))

        with patch(
            "applications.reminders.send_messages_with_history",
            side_effect=TimeoutError("delivery outcome uncertain"),
        ) as send:
            first = self.run_batch(now=now)
            second = self.run_batch(now=now)

        self.assertEqual(first["unknown"], 1)
        self.assertEqual(second["sent"], 0)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(
            ApplicationReminderDelivery.objects.get(application=application).status,
            ApplicationReminderDelivery.Status.UNKNOWN,
        )

    def test_confirmed_pre_send_failure_is_retryable(self):
        now = timezone.now()
        application = self.make_application()
        self.set_progress(application, now - timedelta(hours=48))

        with patch(
            "applications.reminders.render_to_string",
            side_effect=RuntimeError("template rendering failed"),
        ):
            failed = self.run_batch(now=now)

        delivery = ApplicationReminderDelivery.objects.get(application=application)
        self.assertEqual(failed["failed"], 1)
        self.assertEqual(delivery.status, ApplicationReminderDelivery.Status.FAILED)

        retried = self.run_batch(now=now)

        delivery.refresh_from_db()
        self.assertEqual(retried["sent"], 1)
        self.assertEqual(delivery.attempt_count, 2)
        self.assertEqual(delivery.status, ApplicationReminderDelivery.Status.ACCEPTED)

    def test_batch_is_organization_scoped(self):
        now = timezone.now()
        application = Application.objects.create(
            organization=self.other_organization,
            applicant_type=Application.Type.PARENT,
            status=Application.Status.DRAFT,
            email="other@example.test",
            email_verified_at=now,
            applicant_progress_at=now - timedelta(hours=48),
        )

        result = self.run_batch(now=now, organization=self.organization)

        self.assertEqual(result["sent"], 0)
        self.assertEqual(mail.outbox, [])
        self.assertFalse(
            ApplicationReminderDelivery.objects.filter(application=application).exists()
        )

    def test_status_change_after_selection_prevents_claim(self):
        now = timezone.now()
        application = self.make_application()
        self.set_progress(application, now - timedelta(hours=48))
        claim_plan = reminders._claim_plan

        def change_before_claim(organization, application_pk, observed_at):
            Application.objects.filter(pk=application_pk).update(
                status=Application.Status.APPROVED_SIGNED
            )
            return claim_plan(organization, application_pk, observed_at)

        with patch(
            "applications.reminders._claim_plan", side_effect=change_before_claim
        ):
            result = self.run_batch(now=now)

        self.assertEqual(result["sent"], 0)
        self.assertEqual(mail.outbox, [])
        self.assertFalse(
            ApplicationReminderDelivery.objects.filter(application=application).exists()
        )

    def test_recipient_change_after_selection_prevents_claim(self):
        now = timezone.now()
        application = self.make_application()
        self.set_progress(application, now - timedelta(hours=48))
        claim_plan = reminders._claim_plan

        def change_before_claim(organization, application_pk, observed_at):
            Application.objects.filter(pk=application_pk).update(
                email="new-address@example.test",
                email_verified_at=None,
            )
            return claim_plan(organization, application_pk, observed_at)

        with patch(
            "applications.reminders._claim_plan", side_effect=change_before_claim
        ):
            result = self.run_batch(now=now)

        self.assertEqual(result["sent"], 0)
        self.assertEqual(mail.outbox, [])
        self.assertFalse(
            ApplicationReminderDelivery.objects.filter(application=application).exists()
        )

    def test_progress_change_after_selection_prevents_inactivity_claim(self):
        now = timezone.now()
        application = self.make_application()
        self.set_progress(application, now - timedelta(hours=48))
        claim_plan = reminders._claim_plan

        def update_progress_before_claim(organization, application_pk, observed_at):
            Application.objects.filter(pk=application_pk).update(
                applicant_progress_at=observed_at
            )
            return claim_plan(organization, application_pk, observed_at)

        with patch(
            "applications.reminders._claim_plan",
            side_effect=update_progress_before_claim,
        ):
            result = self.run_batch(now=now)

        self.assertEqual(result["sent"], 0)
        self.assertEqual(mail.outbox, [])
        self.assertFalse(
            ApplicationReminderDelivery.objects.filter(application=application).exists()
        )

    def test_deadline_change_after_selection_prevents_claim(self):
        now = timezone.now()
        application = self.make_application(program=self.program)
        self.set_progress(application, now - timedelta(hours=1))
        self.program.applications_close = now.date() + timedelta(days=2)
        self.program.save(update_fields=["applications_close", "updated_at"])
        claim_plan = reminders._claim_plan

        def change_deadline_before_claim(organization, application_pk, observed_at):
            Program.objects.filter(pk=self.program.pk).update(
                applications_close=observed_at.date() + timedelta(days=3)
            )
            return claim_plan(organization, application_pk, observed_at)

        with patch(
            "applications.reminders._claim_plan",
            side_effect=change_deadline_before_claim,
        ):
            result = self.run_batch(now=now)

        self.assertEqual(result["sent"], 0)
        self.assertEqual(mail.outbox, [])
        self.assertFalse(
            ApplicationReminderDelivery.objects.filter(application=application).exists()
        )

    def test_document_completion_after_selection_prevents_missing_form_claim(self):
        now = timezone.now()
        application = self.make_application(
            applicant_type=Application.Type.PARENT,
            status=Application.Status.SUBMITTED,
            program=self.program,
        )
        self.set_progress(application, now - timedelta(hours=48))
        document = ProgramDocument.objects.create(
            program=self.program,
            name="Permission form",
            file="permission-form.pdf",
            is_required=True,
        )
        claim_plan = reminders._claim_plan

        def complete_before_claim(organization, application_pk, observed_at):
            ApplicationDocumentSubmission.objects.create(
                application=application,
                organization=organization,
                document=document,
                file="signed-permission-form.pdf",
            )
            return claim_plan(organization, application_pk, observed_at)

        with patch(
            "applications.reminders._claim_plan", side_effect=complete_before_claim
        ):
            result = self.run_batch(now=now)

        self.assertEqual(result["sent"], 0)
        self.assertEqual(mail.outbox, [])
        self.assertFalse(
            ApplicationReminderDelivery.objects.filter(application=application).exists()
        )

    def test_status_change_after_claim_prevents_transport(self):
        now = timezone.now()
        application = self.make_application()
        self.set_progress(application, now - timedelta(hours=48))
        revalidate_claim = reminders._revalidate_claim

        def change_before_transport(organization, claim, observed_at):
            Application.objects.filter(pk=application.pk).update(
                status=Application.Status.APPROVED_SIGNED
            )
            return revalidate_claim(organization, claim, observed_at)

        with patch(
            "applications.reminders._revalidate_claim",
            side_effect=change_before_transport,
        ):
            result = self.run_batch(now=now)

        self.assertEqual(result["sent"], 0)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(mail.outbox, [])
        self.assertEqual(
            ApplicationReminderDelivery.objects.get(application=application).status,
            ApplicationReminderDelivery.Status.FAILED,
        )
