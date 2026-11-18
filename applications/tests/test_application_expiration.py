from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from applications import expiration
from applications.expiration import run_application_expiration_batch
from applications.models import Application, ApplicationDocumentSubmission
from audit.events import AuditEvent
from audit.models import AuditLog
from organizations.models import Organization
from programs.models import Program, ProgramDocument


class ApplicationExpirationTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(
            name="Expiration Tests",
            slug="expiration-tests",
            subdomain="expiration-tests",
        )
        self.other_organization = Organization.objects.create(
            name="Other Expiration Tests",
            slug="other-expiration-tests",
            subdomain="other-expiration-tests",
        )
        self.program = Program.objects.create(
            organization=self.organization,
            name="Expiration Program",
        )
        self.required_document = ProgramDocument.objects.create(
            program=self.program,
            name="Permission form",
            file="permission-form.pdf",
            is_required=True,
        )

    def make_application(
        self,
        *,
        organization=None,
        applicant_type=Application.Type.PARENT,
        status=Application.Status.DRAFT,
        program=None,
        progress_at=None,
        updated_at=None,
    ):
        application = Application.objects.create(
            organization=organization or self.organization,
            applicant_type=applicant_type,
            status=status,
            program=program,
            email="applicant@example.test",
        )
        changes = {}
        if progress_at is not None:
            changes["applicant_progress_at"] = progress_at
        if updated_at is not None:
            changes["updated_at"] = updated_at
        if changes:
            Application.objects.filter(pk=application.pk).update(**changes)
            application.refresh_from_db()
        return application

    def run_batch(self, *, now=None, execute=False, organization=None, limit=200):
        return run_application_expiration_batch(
            organization or self.organization,
            now=now or timezone.now(),
            execute=execute,
            limit=limit,
        )

    def test_expiration_is_due_at_30_days_and_uses_legacy_timestamp_fallback(self):
        now = timezone.now()
        exact_boundary = self.make_application(progress_at=now - timedelta(days=30))
        recent = self.make_application(
            progress_at=now - timedelta(days=30) + timedelta(microseconds=1)
        )
        legacy = self.make_application()
        Application.objects.filter(pk=legacy.pk).update(
            updated_at=now - timedelta(days=31),
            applicant_progress_at=None,
        )

        result = self.run_batch(now=now)

        self.assertEqual(result["eligible"], 2)
        self.assertTrue(Application.objects.filter(pk=exact_boundary.pk).exists())
        self.assertTrue(Application.objects.filter(pk=recent.pk).exists())
        self.assertTrue(Application.objects.filter(pk=legacy.pk).exists())

    def test_eligible_phases_exclude_completed_forms_and_staff_review(self):
        now = timezone.now()
        old = now - timedelta(days=31)
        draft = self.make_application(progress_at=old)
        handoff = self.make_application(
            applicant_type=Application.Type.STUDENT,
            status=Application.Status.AWAITING_PARENT,
            progress_at=old,
        )
        missing_forms = self.make_application(
            applicant_type=Application.Type.STUDENT,
            status=Application.Status.SUBMITTED,
            program=self.program,
            progress_at=old,
        )
        complete_forms = self.make_application(
            applicant_type=Application.Type.PARENT,
            status=Application.Status.SUBMITTED,
            program=self.program,
            progress_at=old,
        )
        ApplicationDocumentSubmission.objects.create(
            application=complete_forms,
            organization=self.organization,
            document=self.required_document,
            file="private/application_documents/complete.pdf",
        )
        awaiting_review = self.make_application(
            applicant_type=Application.Type.PARENT,
            status=Application.Status.SUBMITTED,
            progress_at=old,
        )
        mentor_submission = self.make_application(
            applicant_type=Application.Type.MENTOR,
            status=Application.Status.SUBMITTED,
            program=self.program,
            progress_at=old,
        )
        finalized = [
            self.make_application(status=status, progress_at=old)
            for status in (
                Application.Status.APPROVED,
                Application.Status.APPROVED_SIGNED,
                Application.Status.CONVERTED,
                Application.Status.DECLINED,
            )
        ]

        result = self.run_batch(now=now)

        self.assertEqual(result["eligible"], 3)
        for application in (
            draft,
            handoff,
            missing_forms,
            complete_forms,
            awaiting_review,
            mentor_submission,
            *finalized,
        ):
            self.assertTrue(Application.objects.filter(pk=application.pk).exists())

    def test_candidates_are_organization_scoped(self):
        old = timezone.now() - timedelta(days=31)
        local = self.make_application(progress_at=old)
        foreign = self.make_application(
            organization=self.other_organization,
            progress_at=old,
        )

        result = self.run_batch()

        self.assertEqual(result["eligible"], 1)
        self.assertTrue(Application.objects.filter(pk=local.pk).exists())
        self.assertTrue(Application.objects.filter(pk=foreign.pk).exists())

    def test_command_is_read_only_without_execute_and_deletes_owned_document_files(
        self,
    ):
        now = timezone.now()
        application = self.make_application(
            applicant_type=Application.Type.STUDENT,
            status=Application.Status.SUBMITTED,
            program=self.program,
            progress_at=now - timedelta(days=31),
        )
        optional_document = ProgramDocument.objects.create(
            program=self.program,
            name="Optional photo release",
            file="photo-release.pdf",
            is_required=False,
        )

        with (
            TemporaryDirectory() as media_root,
            override_settings(MEDIA_ROOT=media_root),
        ):
            relative_path = (
                f"private/application_documents/{self.organization.pk}/"
                f"{application.application_id}/signed.pdf"
            )
            saved_path = default_storage.save(
                relative_path, ContentFile(b"signed form")
            )
            ApplicationDocumentSubmission.objects.create(
                application=application,
                organization=self.organization,
                document=optional_document,
                file=saved_path,
            )

            call_command("expire_applications", organization=self.organization.slug)

            self.assertTrue(Application.objects.filter(pk=application.pk).exists())
            self.assertTrue(default_storage.exists(saved_path))

            call_command(
                "expire_applications",
                organization=self.organization.slug,
                execute=True,
            )

            self.assertFalse(Application.objects.filter(pk=application.pk).exists())
            self.assertFalse(default_storage.exists(saved_path))
            self.assertFalse(Path(media_root, relative_path).exists())
            self.assertTrue(
                AuditLog.objects.filter(
                    event=AuditEvent.RECORD_DELETED,
                    resource_type="Application",
                    resource_id=str(application.pk),
                ).exists()
            )

    def test_inconsistent_document_ownership_prevents_application_deletion(self):
        now = timezone.now()
        application = self.make_application(
            applicant_type=Application.Type.STUDENT,
            status=Application.Status.SUBMITTED,
            program=self.program,
            progress_at=now - timedelta(days=31),
        )
        foreign_program = Program.objects.create(
            organization=self.other_organization,
            name="Foreign Program",
        )
        foreign_document = ProgramDocument.objects.create(
            program=foreign_program,
            name="Foreign form",
            file="foreign-form.pdf",
            is_required=True,
        )
        ApplicationDocumentSubmission.objects.create(
            application=application,
            organization=self.other_organization,
            document=foreign_document,
            file="private/application_documents/foreign/signed.pdf",
        )

        result = self.run_batch(execute=True, now=now)

        self.assertEqual(result["deleted"], 0)
        self.assertEqual(result["failed"], 1)
        self.assertTrue(Application.objects.filter(pk=application.pk).exists())

    def test_foreign_program_prevents_application_deletion(self):
        now = timezone.now()
        foreign_program = Program.objects.create(
            organization=self.other_organization,
            name="Foreign Program",
        )
        application = self.make_application(
            program=foreign_program,
            progress_at=now - timedelta(days=31),
        )

        result = self.run_batch(now=now, execute=True)

        self.assertEqual(result["deleted"], 0)
        self.assertEqual(result["failed"], 1)
        self.assertTrue(Application.objects.filter(pk=application.pk).exists())

    def test_document_outside_application_storage_prefix_is_not_deleted(self):
        now = timezone.now()
        application = self.make_application(
            applicant_type=Application.Type.STUDENT,
            status=Application.Status.SUBMITTED,
            program=self.program,
            progress_at=now - timedelta(days=31),
        )
        optional_document = ProgramDocument.objects.create(
            program=self.program,
            name="Optional form",
            file="optional-form.pdf",
            is_required=False,
        )
        ApplicationDocumentSubmission.objects.create(
            application=application,
            organization=self.organization,
            document=optional_document,
            file="private/application_documents/another-application/signed.pdf",
        )

        result = self.run_batch(now=now, execute=True)

        self.assertEqual(result["deleted"], 0)
        self.assertEqual(result["failed"], 1)
        self.assertTrue(Application.objects.filter(pk=application.pk).exists())

    def test_locked_recheck_skips_new_progress_approval_and_completed_forms(self):
        now = timezone.now()
        old = now - timedelta(days=31)
        progress_changed = self.make_application(progress_at=old)
        approved = self.make_application(progress_at=old)
        forms_completed = self.make_application(
            applicant_type=Application.Type.STUDENT,
            status=Application.Status.SUBMITTED,
            program=self.program,
            progress_at=old,
        )
        original_expire_locked = expiration._expire_locked

        def intervene_then_expire(organization, application_pk, cutoff):
            if application_pk == progress_changed.pk:
                Application.objects.filter(pk=application_pk).update(
                    applicant_progress_at=now
                )
            elif application_pk == approved.pk:
                Application.objects.filter(pk=application_pk).update(
                    status=Application.Status.APPROVED
                )
            elif application_pk == forms_completed.pk:
                ApplicationDocumentSubmission.objects.create(
                    application=forms_completed,
                    organization=self.organization,
                    document=self.required_document,
                    file=(
                        f"private/application_documents/{self.organization.pk}/"
                        f"{forms_completed.application_id}/signed.pdf"
                    ),
                )
            return original_expire_locked(organization, application_pk, cutoff)

        with patch(
            "applications.expiration._expire_locked",
            side_effect=intervene_then_expire,
        ):
            result = self.run_batch(now=now, execute=True)

        self.assertEqual(result["deleted"], 0)
        self.assertEqual(result["skipped"], 3)
        for application in (progress_changed, approved, forms_completed):
            self.assertTrue(Application.objects.filter(pk=application.pk).exists())

    def test_audit_failure_prevents_application_deletion(self):
        application = self.make_application(
            progress_at=timezone.now() - timedelta(days=31)
        )

        with patch("applications.expiration.log_deletion", return_value=None):
            result = self.run_batch(execute=True)

        self.assertEqual(result["deleted"], 0)
        self.assertEqual(result["failed"], 1)
        self.assertTrue(Application.objects.filter(pk=application.pk).exists())

    def test_storage_delete_failure_keeps_application_and_reports_failure(self):
        now = timezone.now()
        application = self.make_application(
            applicant_type=Application.Type.STUDENT,
            status=Application.Status.SUBMITTED,
            program=self.program,
            progress_at=now - timedelta(days=31),
        )
        optional_document = ProgramDocument.objects.create(
            program=self.program,
            name="Optional form",
            file="optional-form.pdf",
            is_required=False,
        )

        with (
            TemporaryDirectory() as media_root,
            override_settings(MEDIA_ROOT=media_root),
        ):
            relative_path = (
                f"private/application_documents/{self.organization.pk}/"
                f"{application.application_id}/optional.pdf"
            )
            saved_path = default_storage.save(
                relative_path, ContentFile(b"optional form")
            )
            ApplicationDocumentSubmission.objects.create(
                application=application,
                organization=self.organization,
                document=optional_document,
                file=saved_path,
            )

            class FailingStorage:
                def delete(self, name):
                    raise OSError("storage unavailable")

            with patch(
                "applications.expiration._owned_document_files",
                return_value=(FailingStorage(), [saved_path]),
            ):
                result = self.run_batch(now=now, execute=True)

            self.assertEqual(result["deleted"], 0)
            self.assertEqual(result["failed"], 1)
            self.assertTrue(Application.objects.filter(pk=application.pk).exists())
            self.assertTrue(default_storage.exists(saved_path))

    def test_batch_limit_bounds_processed_candidates(self):
        old = timezone.now() - timedelta(days=31)
        self.make_application(progress_at=old)
        self.make_application(progress_at=old)

        result = self.run_batch(limit=1)

        self.assertEqual(result["processed"], 1)
        self.assertEqual(result["eligible"], 1)
