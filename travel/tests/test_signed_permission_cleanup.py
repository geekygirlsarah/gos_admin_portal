from datetime import date
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.files.base import ContentFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase, TransactionTestCase, override_settings

from organizations.models import Organization
from programs.models import Program, School, Student
from travel.models import (
    TravelEvent,
    TravelSignedFileDeletion,
    TravelSignedPermissionForm,
    TravelSignup,
)


class SignedPermissionCleanupTests(TestCase):
    def setUp(self):
        self.media = TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        override = override_settings(MEDIA_ROOT=self.media.name)
        override.enable()
        self.addCleanup(override.disable)
        self.organization = Organization.objects.get(slug="gos")
        self.foreign = Organization.objects.create(
            name="Other", slug="cleanup-other", subdomain="cleanup-other"
        )
        self.program = Program.objects.create(
            organization=self.organization, name="Cleanup program"
        )
        self.school = School.objects.create(name="Cleanup school")
        self.student = Student.objects.create(
            organization=self.organization,
            legal_first_name="Test",
            last_name="Student",
            school=self.school,
            graduation_year=2027,
        )
        self.event = self.make_event(self.program)
        self.signup = TravelSignup.objects.create(
            organization=self.organization, event=self.event, student=self.student
        )
        self.storage = TravelSignedPermissionForm._meta.get_field("file").storage
        self.name = f"private/travel/signed_permissions/{self.organization.pk}/form.pdf"
        self.storage.save(self.name, ContentFile(b"test ciphertext"))
        self.signed = self.make_signed(self.signup, self.organization, self.name)

    def make_event(self, program):
        return TravelEvent.objects.create(
            organization=program.organization,
            program=program,
            name="Cleanup trip",
            start_date=date(2027, 1, 1),
            end_date=date(2027, 1, 2),
        )

    def make_signed(self, signup, organization, name):
        return TravelSignedPermissionForm.objects.create(
            organization=organization,
            signup=signup,
            form_version=signup.event.permission_form_version,
            file=name,
        )

    def test_event_and_program_instance_and_queryset_cascades(self):
        for origin in ("event", "event_queryset", "program", "program_queryset"):
            with self.subTest(origin=origin):
                if origin != "event":
                    self.event = self.make_event(self.program)
                    self.signup = TravelSignup.objects.create(
                        organization=self.organization,
                        event=self.event,
                        student=self.student,
                    )
                    self.storage.save(self.name, ContentFile(b"test ciphertext"))
                    self.make_signed(self.signup, self.organization, self.name)
                with self.captureOnCommitCallbacks(execute=True) as callbacks:
                    if origin == "event":
                        self.event.delete()
                    elif origin == "event_queryset":
                        TravelEvent.objects.filter(pk=self.event.pk).delete()
                    elif origin == "program":
                        self.program.delete()
                    else:
                        Program.objects.filter(pk=self.program.pk).delete()
                    self.assertTrue(self.storage.exists(self.name))
                    self.assertTrue(
                        TravelSignedFileDeletion.objects.filter(
                            file_name=self.name
                        ).exists()
                    )
                self.assertEqual(len(callbacks), 1)
                self.assertFalse(self.storage.exists(self.name))
                self.assertFalse(
                    TravelSignedFileDeletion.objects.filter(
                        file_name=self.name
                    ).exists()
                )
                if origin == "program":
                    self.program = Program.objects.create(
                        organization=self.organization, name="Another program"
                    )

    def test_student_signup_and_signed_form_deletions_are_protected(self):
        for origin in (
            lambda: self.student.delete(),
            lambda: Student.objects.filter(pk=self.student.pk).delete(),
            lambda: self.signup.delete(),
            lambda: self.signed.delete(),
            lambda: TravelSignedPermissionForm.objects.filter(
                pk=self.signed.pk
            ).delete(),
        ):
            with self.subTest(origin=origin):
                with self.assertRaises(ProtectedError) as error:
                    with transaction.atomic():
                        origin()
                self.assertIn(self.signed, error.exception.protected_objects)
                self.assertTrue(
                    TravelSignedPermissionForm.objects.filter(
                        pk=self.signed.pk
                    ).exists()
                )
                self.assertTrue(self.storage.exists(self.name))
                self.assertFalse(TravelSignedFileDeletion.objects.exists())

    def test_rollback_restores_record_and_does_not_delete_file(self):
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            with self.assertRaisesMessage(ValueError, "rollback"):
                with transaction.atomic():
                    self.event.delete()
                    self.assertTrue(
                        TravelSignedFileDeletion.objects.filter(
                            file_name=self.name
                        ).exists()
                    )
                    raise ValueError("rollback")
        self.assertEqual(callbacks, [])
        self.assertTrue(
            TravelSignedPermissionForm.objects.filter(pk=self.signed.pk).exists()
        )
        self.assertFalse(TravelSignedFileDeletion.objects.exists())
        self.assertTrue(self.storage.exists(self.name))

    def test_failure_retains_queue_and_scoped_retry_succeeds(self):
        foreign_name = (
            f"private/travel/signed_permissions/{self.foreign.pk}/foreign.pdf"
        )
        self.storage.save(foreign_name, ContentFile(b"foreign ciphertext"))
        TravelSignedFileDeletion.objects.create(
            organization=self.foreign, file_name=foreign_name
        )
        with patch.object(
            self.storage, "delete", side_effect=OSError("sensitive path")
        ):
            with self.assertLogs("travel.cleanup", level="WARNING") as logs:
                with self.captureOnCommitCallbacks(execute=True):
                    self.event.delete()
            self.assertNotIn("sensitive path", " ".join(logs.output))
        self.assertTrue(
            TravelSignedFileDeletion.objects.filter(file_name=self.name).exists()
        )
        call_command(
            "retry_signed_permission_cleanup",
            organization_id=self.organization.pk,
            stdout=StringIO(),
        )
        self.assertFalse(self.storage.exists(self.name))
        self.assertFalse(
            TravelSignedFileDeletion.objects.filter(file_name=self.name).exists()
        )
        self.assertTrue(self.storage.exists(foreign_name))
        self.assertTrue(
            TravelSignedFileDeletion.objects.filter(file_name=foreign_name).exists()
        )

    def test_retry_requires_organization(self):
        with self.assertRaises(CommandError):
            call_command("retry_signed_permission_cleanup", stdout=StringIO())
        with self.assertRaises(CommandError):
            call_command(
                "retry_signed_permission_cleanup",
                organization_id=999999,
                stdout=StringIO(),
            )

    def test_duplicate_queued_key_is_deleted_only_once(self):
        self.make_signed(self.signup, self.organization, self.name)
        with patch.object(self.storage, "delete", wraps=self.storage.delete) as delete:
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                self.event.delete()
                self.assertEqual(TravelSignedFileDeletion.objects.count(), 1)
            self.assertEqual(len(callbacks), 2)
            delete.assert_called_once_with(self.name)
        self.assertFalse(TravelSignedFileDeletion.objects.exists())
        self.assertFalse(self.storage.exists(self.name))

    def test_conflicting_queue_owner_is_not_reassigned_or_deleted(self):
        pending = TravelSignedFileDeletion.objects.create(
            organization=self.foreign, file_name=self.name
        )
        with patch.object(self.storage, "delete") as delete:
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                self.event.delete()
            self.assertEqual(callbacks, [])
            delete.assert_not_called()
        pending.refresh_from_db()
        self.assertEqual(pending.organization_id, self.foreign.pk)
        self.assertTrue(self.storage.exists(self.name))

    def test_invalid_deleted_form_path_stays_queued(self):
        invalid_name = f"private/travel/signed_permissions/{self.foreign.pk}/other.pdf"
        TravelSignedPermissionForm.objects.filter(pk=self.signed.pk).update(
            file=invalid_name
        )
        with patch.object(self.storage, "delete") as delete:
            with self.captureOnCommitCallbacks(execute=True):
                self.event.delete()
            delete.assert_not_called()
        self.assertTrue(
            TravelSignedFileDeletion.objects.filter(
                organization=self.organization, file_name=invalid_name
            ).exists()
        )

    def test_symlinked_storage_directory_is_not_deleted(self):
        with patch.object(Path, "is_symlink", return_value=True):
            with patch.object(self.storage, "delete") as delete:
                with self.captureOnCommitCallbacks(execute=True):
                    self.event.delete()
                delete.assert_not_called()
        self.assertTrue(
            TravelSignedFileDeletion.objects.filter(file_name=self.name).exists()
        )

    def test_retry_handles_missing_file_idempotently(self):
        missing_name = (
            f"private/travel/signed_permissions/{self.organization.pk}/missing.pdf"
        )
        TravelSignedFileDeletion.objects.create(
            organization=self.organization, file_name=missing_name
        )
        call_command(
            "retry_signed_permission_cleanup",
            organization_id=self.organization.pk,
            stdout=StringIO(),
        )
        self.assertFalse(
            TravelSignedFileDeletion.objects.filter(file_name=missing_name).exists()
        )

    def test_shared_key_with_foreign_reference_is_not_deleted(self):
        other_event = self.make_event(self.program)
        other_signup = TravelSignup.objects.create(
            organization=self.organization, event=other_event, student=self.student
        )
        foreign_record = self.make_signed(other_signup, self.foreign, self.name)
        with self.captureOnCommitCallbacks(execute=True):
            self.event.delete()
        self.assertTrue(self.storage.exists(self.name))
        self.assertTrue(
            TravelSignedFileDeletion.objects.filter(file_name=self.name).exists()
        )
        with patch.object(self.storage, "delete") as delete:
            call_command(
                "retry_signed_permission_cleanup",
                organization_id=self.organization.pk,
                stdout=StringIO(),
            )
            delete.assert_not_called()
        with self.assertRaises(ProtectedError):
            with transaction.atomic():
                other_event.delete()
        self.assertTrue(self.storage.exists(self.name))
        self.assertTrue(
            TravelSignedPermissionForm.objects.filter(pk=foreign_record.pk).exists()
        )
        TravelSignedPermissionForm.objects.filter(pk=foreign_record.pk).update(
            organization=self.organization
        )
        with self.captureOnCommitCallbacks(execute=True):
            other_event.delete()
        call_command(
            "retry_signed_permission_cleanup",
            organization_id=self.organization.pk,
            stdout=StringIO(),
        )
        self.assertFalse(self.storage.exists(self.name))
        self.assertFalse(
            TravelSignedFileDeletion.objects.filter(file_name=self.name).exists()
        )

    def test_invalid_paths_are_retained_without_storage_deletion(self):
        names = [
            f"private/travel/signed_permissions/{self.foreign.pk}/foreign.pdf",
            f"private/travel/signed_permissions/{self.organization.pk}/../escape.pdf",
            f"private/travel/signed_permissions/{self.organization.pk}/subdir/file.pdf",
            f"private/travel/signed_permissions/{self.organization.pk}/..\\escape.pdf",
            f"/private/travel/signed_permissions/{self.organization.pk}/form.pdf",
        ]
        for name in names:
            TravelSignedFileDeletion.objects.create(
                organization=self.organization, file_name=name
            )
        with patch.object(self.storage, "delete") as delete:
            call_command(
                "retry_signed_permission_cleanup",
                organization_id=self.organization.pk,
                stdout=StringIO(),
            )
            delete.assert_not_called()
        self.assertEqual(TravelSignedFileDeletion.objects.count(), len(names))


class SignedPermissionCommitTests(TransactionTestCase):
    def test_committed_delete_runs_cleanup(self):
        with TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            organization = Organization.objects.create(
                name="Commit", slug="cleanup-commit", subdomain="cleanup-commit"
            )
            program = Program.objects.create(organization=organization, name="Commit")
            school = School.objects.create(name="Commit school")
            student = Student.objects.create(
                organization=organization,
                legal_first_name="Test",
                last_name="Student",
                school=school,
                graduation_year=2027,
            )
            event = TravelEvent.objects.create(
                organization=organization,
                program=program,
                name="Commit trip",
                start_date=date(2027, 1, 1),
                end_date=date(2027, 1, 2),
            )
            signup = TravelSignup.objects.create(
                organization=organization, event=event, student=student
            )
            name = f"private/travel/signed_permissions/{organization.pk}/commit.pdf"
            storage = TravelSignedPermissionForm._meta.get_field("file").storage
            storage.save(name, ContentFile(b"test ciphertext"))
            TravelSignedPermissionForm.objects.create(
                organization=organization,
                signup=signup,
                form_version=event.permission_form_version,
                file=name,
            )
            with transaction.atomic():
                event.delete()
                self.assertTrue(storage.exists(name))
                self.assertTrue(
                    TravelSignedFileDeletion.objects.filter(file_name=name).exists()
                )
            self.assertFalse(storage.exists(name))
            self.assertFalse(
                TravelSignedFileDeletion.objects.filter(file_name=name).exists()
            )
