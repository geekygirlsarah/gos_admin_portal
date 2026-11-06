from __future__ import annotations

import io
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from applications.models import Application, ApplicationDocumentSubmission
from organizations.models import Organization
from programs.management.commands import transition_private_media
from programs.models import Program, ProgramDocument, Student


class TransitionPrivateMediaCommandTests(TestCase):
    def setUp(self):
        self.media_dir = tempfile.TemporaryDirectory(prefix="private-media-transition-")
        self.addCleanup(self.media_dir.cleanup)
        settings_override = override_settings(MEDIA_ROOT=self.media_dir.name)
        settings_override.enable()
        self.addCleanup(settings_override.disable)

        self.organization = Organization.objects.create(
            name="Transition Alpha",
            slug="transition-alpha",
            subdomain="transition-alpha",
        )
        self.foreign_organization = Organization.objects.create(
            name="Transition Beta",
            slug="transition-beta",
            subdomain="transition-beta",
        )
        self.program = Program.objects.create(
            organization=self.organization,
            name="Transition Program",
        )
        self.program_document = ProgramDocument.objects.create(
            program=self.program,
            name="Release form",
            file=SimpleUploadedFile("blank.pdf", b"blank form"),
            is_required=True,
        )

    @property
    def media_root(self):
        return Path(self.media_dir.name)

    def create_student(self, *, photo_path="photos/students/legacy-photo.jpg"):
        return Student.objects.create(
            organization=self.organization,
            legal_first_name="Sensitive",
            last_name="Student",
            photo=photo_path,
        )

    def create_submission(
        self,
        *,
        organization=None,
        application_organization=None,
        document=None,
        file_path="application_documents/legacy/legacy-signed.pdf",
    ):
        owner = organization or self.organization
        app = Application.objects.create(
            organization=application_organization or self.organization,
            applicant_type=Application.Type.PARENT,
            email="applicant@example.test",
            program=self.program,
            status=Application.Status.APPROVED,
        )
        submission = ApplicationDocumentSubmission.objects.create(
            organization=owner,
            application=app,
            document=document or self.program_document,
            file=file_path,
        )
        return app, submission

    def write_source(self, relative_path, content):
        source = self.media_root / relative_path
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(content)
        return source

    @staticmethod
    def run_transition(*, execute=False):
        output = io.StringIO()
        call_command(
            "transition_private_media",
            *(["--execute"] if execute else []),
            stdout=output,
        )
        return output.getvalue()

    def assert_transition_requires_review(self, *args, **kwargs):
        with self.assertRaises(CommandError) as raised:
            self.run_transition(*args, **kwargs)
        self.assertNotIn("Unknown command", str(raised.exception))

    def test_default_run_is_read_only_and_reports_without_sensitive_names(self):
        student = self.create_student()
        _, submission = self.create_submission()
        photo_source = self.write_source(student.photo.name, b"private photo bytes")
        document_source = self.write_source(
            submission.file.name, b"signed document bytes"
        )

        output = self.run_transition()

        student.refresh_from_db()
        submission.refresh_from_db()
        self.assertEqual(student.photo.name, "photos/students/legacy-photo.jpg")
        self.assertEqual(
            submission.file.name,
            "application_documents/legacy/legacy-signed.pdf",
        )
        self.assertTrue(photo_source.exists())
        self.assertTrue(document_source.exists())
        self.assertNotIn("Sensitive", output)
        self.assertNotIn("applicant@example.test", output)
        self.assertNotIn("legacy-photo.jpg", output)
        self.assertNotIn("legacy-signed.pdf", output)
        self.assertIn("dry", output.lower())

    def test_execute_copies_and_updates_photos_and_signed_documents(self):
        student = self.create_student()
        _, submission = self.create_submission()
        photo_bytes = b"private photo bytes"
        signed_bytes = b"signed document bytes"
        photo_source = self.write_source(student.photo.name, photo_bytes)
        document_source = self.write_source(submission.file.name, signed_bytes)

        self.run_transition(execute=True)

        student.refresh_from_db()
        submission.refresh_from_db()
        self.assertTrue(student.photo.name.startswith("private/student_photos/"))
        self.assertTrue(
            submission.file.name.startswith("private/application_documents/")
        )
        self.assertNotIn("legacy-photo", student.photo.name)
        self.assertNotIn("legacy-signed", submission.file.name)
        self.assertEqual(
            (self.media_root / student.photo.name).read_bytes(), photo_bytes
        )
        self.assertEqual(
            (self.media_root / submission.file.name).read_bytes(), signed_bytes
        )
        self.assertTrue(photo_source.exists())
        self.assertTrue(document_source.exists())

    def test_shared_source_creates_independent_private_copies(self):
        shared_path = "photos/students/shared.jpg"
        first = self.create_student(photo_path=shared_path)
        second = self.create_student(photo_path=shared_path)
        source = self.write_source(shared_path, b"shared source bytes")

        self.run_transition(execute=True)

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertNotEqual(first.photo.name, second.photo.name)
        self.assertEqual(
            (self.media_root / first.photo.name).read_bytes(), b"shared source bytes"
        )
        self.assertEqual(
            (self.media_root / second.photo.name).read_bytes(), b"shared source bytes"
        )
        self.assertTrue(source.exists())

    def test_repeated_execution_is_idempotent(self):
        student = self.create_student()
        source = self.write_source(student.photo.name, b"photo bytes")

        self.run_transition(execute=True)
        student.refresh_from_db()
        first_private_path = student.photo.name
        first_private_files = sorted(
            path.relative_to(self.media_root).as_posix()
            for path in (self.media_root / "private/student_photos").rglob("*")
            if path.is_file()
        )
        self.run_transition(execute=True)

        student.refresh_from_db()
        second_private_files = sorted(
            path.relative_to(self.media_root).as_posix()
            for path in (self.media_root / "private/student_photos").rglob("*")
            if path.is_file()
        )
        self.assertEqual(student.photo.name, first_private_path)
        self.assertEqual(second_private_files, first_private_files)
        self.assertTrue(source.exists())

    def test_missing_source_is_not_updated_and_execution_fails(self):
        student = self.create_student()

        self.assert_transition_requires_review(execute=True)

        student.refresh_from_db()
        self.assertEqual(student.photo.name, "photos/students/legacy-photo.jpg")

    def test_inconsistent_submission_ownership_is_not_transitioned(self):
        _, submission = self.create_submission(organization=self.foreign_organization)
        source = self.write_source(submission.file.name, b"foreign signed bytes")

        self.assert_transition_requires_review(execute=True)

        submission.refresh_from_db()
        self.assertEqual(
            submission.file.name,
            "application_documents/legacy/legacy-signed.pdf",
        )
        self.assertTrue(source.exists())

    def test_submission_with_foreign_application_owner_is_not_transitioned(self):
        _, submission = self.create_submission(
            application_organization=self.foreign_organization
        )
        source = self.write_source(submission.file.name, b"mismatched signed bytes")

        self.assert_transition_requires_review(execute=True)

        submission.refresh_from_db()
        self.assertEqual(
            submission.file.name,
            "application_documents/legacy/legacy-signed.pdf",
        )
        self.assertTrue(source.exists())

    def test_unexpected_or_unsafe_source_path_is_not_opened_or_updated(self):
        student = self.create_student(photo_path="../public/secret.jpg")
        outside_source = self.write_source("public/secret.jpg", b"must not copy")

        self.assert_transition_requires_review(execute=True)

        student.refresh_from_db()
        self.assertEqual(student.photo.name, "../public/secret.jpg")
        self.assertTrue(outside_source.exists())
        self.assertFalse((self.media_root / "private/student_photos").exists())

    def test_foreign_program_document_is_not_transitioned(self):
        foreign_program = Program.objects.create(
            organization=self.foreign_organization,
            name="Foreign Program",
        )
        foreign_document = ProgramDocument.objects.create(
            program=foreign_program,
            name="Foreign release form",
            file=SimpleUploadedFile("foreign.pdf", b"foreign blank form"),
            is_required=True,
        )
        _, submission = self.create_submission(document=foreign_document)
        source = self.write_source(submission.file.name, b"mismatched program bytes")

        self.assert_transition_requires_review(execute=True)

        submission.refresh_from_db()
        self.assertEqual(
            submission.file.name,
            "application_documents/legacy/legacy-signed.pdf",
        )
        self.assertTrue(source.exists())

    def test_copy_verification_failure_does_not_update_reference_or_delete_source(self):
        student = self.create_student()
        source = self.write_source(student.photo.name, b"original photo bytes")
        source_digest = transition_private_media._sha256_file(source)

        with patch.object(
            transition_private_media,
            "_sha256_file",
            side_effect=[source_digest, "incorrect-destination-digest"],
        ):
            self.assert_transition_requires_review(execute=True)

        student.refresh_from_db()
        self.assertEqual(student.photo.name, "photos/students/legacy-photo.jpg")
        self.assertTrue(source.exists())

    def test_unsupported_storage_is_rejected(self):
        student = self.create_student()
        source = self.write_source(student.photo.name, b"photo bytes")
        photo_field = Student._meta.get_field("photo")

        with patch.multiple(photo_field, storage=object()):
            self.assert_transition_requires_review(execute=True)

        student.refresh_from_db()
        self.assertEqual(student.photo.name, "photos/students/legacy-photo.jpg")
        self.assertTrue(source.exists())

    def test_changed_record_reference_is_not_overwritten_after_copy(self):
        student = self.create_student()
        source = self.write_source(student.photo.name, b"photo bytes")
        real_copy_and_verify = transition_private_media._copy_and_verify

        def copy_then_change_reference(*args, **kwargs):
            result = real_copy_and_verify(*args, **kwargs)
            Student.objects.filter(pk=student.pk).update(
                photo="photos/students/updated-during-copy.jpg"
            )
            return result

        with patch.object(
            transition_private_media,
            "_copy_and_verify",
            side_effect=copy_then_change_reference,
        ):
            self.assert_transition_requires_review(execute=True)

        student.refresh_from_db()
        self.assertEqual(student.photo.name, "photos/students/updated-during-copy.jpg")
        self.assertTrue(source.exists())
