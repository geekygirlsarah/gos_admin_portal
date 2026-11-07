import hashlib
import io
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from organizations.models import Organization
from programs.management.commands import cleanup_student_photos
from programs.models import Adult, Student, StudentPhotoRetentionInventory


class StudentPhotoCleanupCommandTests(TestCase):
    def setUp(self):
        self.media_dir = tempfile.TemporaryDirectory(prefix="student-photo-cleanup-")
        self.addCleanup(self.media_dir.cleanup)
        settings_override = override_settings(MEDIA_ROOT=self.media_dir.name)
        settings_override.enable()
        self.addCleanup(settings_override.disable)

        self.organization = Organization.objects.create(
            name="Photo Retention Organization",
            slug="photo-retention",
            subdomain="photo-retention",
        )

    @property
    def media_root(self):
        return Path(self.media_dir.name)

    def private_photo_path(self, filename, organization=None):
        owner = organization or self.organization
        return f"private/student_photos/{owner.pk}/{filename}"

    def write_photo(self, name, content=b"student photo"):
        path = self.media_root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def create_student(self, photo=""):
        return Student.objects.create(
            organization=self.organization,
            legal_first_name="Sample",
            last_name="Student",
            photo=photo,
        )

    def create_adult(self, photo="", organization=None):
        return Adult.objects.create(
            organization=organization or self.organization,
            legal_first_name="Sample",
            last_name="Adult",
            photo=photo,
        )

    def inventory_photo(self, name, content, *, days_ago=0, first_seen=None):
        return StudentPhotoRetentionInventory.objects.create(
            organization=self.organization,
            path=name,
            size=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            first_unreferenced_at=first_seen
            or timezone.now() - timedelta(days=days_ago),
        )

    @staticmethod
    def run_command(*args):
        output = io.StringIO()
        call_command("cleanup_student_photos", *args, stdout=output)
        return output.getvalue()

    @staticmethod
    def create_photo_event(student, photo_path, created_at):
        event_model = next(
            model
            for model in cleanup_student_photos._photo_history_models()
            if model.pgh_tracked_model is Student
        )
        event_field_names = {field.attname for field in event_model._meta.fields}
        event_data = {
            field.attname: getattr(student, field.attname)
            for field in Student._meta.fields
            if field.attname in event_field_names
        }
        event_data.update(
            pgh_label="retention-test",
            pgh_obj=student,
            photo=photo_path,
        )
        event = event_model._base_manager.create(**event_data)
        event_model._base_manager.filter(pk=event.pk).update(pgh_created_at=created_at)
        return event_model._base_manager.get(pk=event.pk)

    def test_default_command_is_a_read_only_preview(self):
        name = self.private_photo_path("orphan.jpg")
        photo = self.write_photo(name)
        output = self.run_command()

        self.assertIn("dry run", output.lower())
        self.assertIn("awaiting inventory=1", output)
        self.assertTrue(photo.exists())
        self.assertFalse(StudentPhotoRetentionInventory.objects.exists())

    def test_inventory_starts_a_fresh_90_day_window_without_deleting(self):
        name = self.private_photo_path("orphan.jpg")
        content = b"orphan photo"
        photo = self.write_photo(name, content)
        started = timezone.now()

        output = self.run_command("--inventory")

        entry = StudentPhotoRetentionInventory.objects.get(path=name)
        self.assertGreaterEqual(entry.first_unreferenced_at, started)
        self.assertEqual(entry.sha256, hashlib.sha256(content).hexdigest())
        self.assertEqual(entry.size, len(content))
        self.assertTrue(photo.exists())
        self.assertIn("newly inventoried=1", output)
        self.assertIn("no photo files were deleted", output)

    def test_replaced_photo_is_retained_until_inventory_and_expiry(self):
        old_name = self.private_photo_path("old.jpg")
        current_name = self.private_photo_path("current.jpg")
        old_photo = self.write_photo(old_name, b"old photo")
        self.write_photo(current_name, b"current photo")
        student = self.create_student(photo=old_name)

        student.photo = current_name
        student.save(update_fields=["photo"])

        self.assertTrue(old_photo.exists())
        self.assertFalse(
            StudentPhotoRetentionInventory.objects.filter(path=old_name).exists()
        )
        started = timezone.now()
        self.run_command("--inventory")
        entry = StudentPhotoRetentionInventory.objects.get(path=old_name)
        self.assertGreaterEqual(entry.first_unreferenced_at, started)
        self.assertTrue(old_photo.exists())
        self.assertEqual(
            Student.objects.values_list("photo", flat=True).get(pk=student.pk),
            current_name,
        )

    def test_exactly_90_days_is_eligible_and_history_path_is_retained(self):
        name = self.private_photo_path("old.jpg")
        content = b"old student photo"
        photo = self.write_photo(name, content)
        student = self.create_student(photo=self.private_photo_path("current.jpg"))
        old_history_time = timezone.now() - timedelta(days=100)
        history = self.create_photo_event(student, name, old_history_time)
        now = timezone.now()
        self.inventory_photo(
            name,
            content,
            first_seen=now - cleanup_student_photos.RETENTION_PERIOD,
        )

        with patch.object(cleanup_student_photos.timezone, "now", return_value=now):
            output = self.run_command(
                "--execute", "--confirm-no-writers", "--confirm-backup"
            )

        self.assertFalse(photo.exists())
        self.assertFalse(
            StudentPhotoRetentionInventory.objects.filter(path=name).exists()
        )
        history.refresh_from_db()
        self.assertEqual(history.photo, name)
        self.assertIn("deleted=1", output)

    def test_photo_within_90_days_is_not_deleted(self):
        name = self.private_photo_path("recent.jpg")
        content = b"recent student photo"
        photo = self.write_photo(name, content)
        self.inventory_photo(name, content, days_ago=89)

        output = self.run_command(
            "--execute", "--confirm-no-writers", "--confirm-backup"
        )

        self.assertTrue(photo.exists())
        self.assertTrue(
            StudentPhotoRetentionInventory.objects.filter(path=name).exists()
        )
        self.assertIn("waiting for inventory or retention=1", output)

    def test_current_student_or_adult_reference_protects_a_shared_photo(self):
        name = self.private_photo_path("shared.jpg")
        photo = self.write_photo(name)
        student = self.create_student(photo=name)
        foreign_organization = Organization.objects.create(
            name="Foreign Photo Organization",
            slug="foreign-photo-org",
            subdomain="foreign-photo-org",
        )
        adult = self.create_adult(photo=name, organization=foreign_organization)
        student.photo = ""
        student.save(update_fields=["photo"])

        output = self.run_command("--inventory")

        self.assertFalse(
            StudentPhotoRetentionInventory.objects.filter(path=name).exists()
        )
        self.assertTrue(photo.exists())
        self.assertEqual(adult.photo.name, name)
        self.assertIn("currently referenced=1", output)

    def test_reuse_in_photo_history_restarts_the_retention_window(self):
        name = self.private_photo_path("reused.jpg")
        content = b"reused student photo"
        photo = self.write_photo(name, content)
        student = self.create_student(photo=self.private_photo_path("current.jpg"))
        self.inventory_photo(name, content, days_ago=100)
        now = timezone.now()
        self.create_photo_event(student, name, now - timedelta(days=2))

        with patch.object(cleanup_student_photos.timezone, "now", return_value=now):
            output = self.run_command("--inventory")

        entry = StudentPhotoRetentionInventory.objects.get(path=name)
        self.assertEqual(entry.first_unreferenced_at, now)
        self.assertTrue(photo.exists())
        self.assertIn("retention clock restarted=1", output)

    def test_changed_file_is_not_deleted_until_reinventoried(self):
        name = self.private_photo_path("changed.jpg")
        original = b"original bytes"
        current = b"changed bytes"
        photo = self.write_photo(name, current)
        entry = self.inventory_photo(name, original, days_ago=100)
        original_first_seen = entry.first_unreferenced_at

        output = self.run_command(
            "--execute", "--confirm-no-writers", "--confirm-backup"
        )

        self.assertTrue(photo.exists())
        entry.refresh_from_db()
        self.assertEqual(entry.first_unreferenced_at, original_first_seen)
        self.assertIn("changed or reused; reinventory required=1", output)

        now = timezone.now()
        with patch.object(cleanup_student_photos.timezone, "now", return_value=now):
            self.run_command("--inventory")
        entry.refresh_from_db()
        self.assertEqual(entry.first_unreferenced_at, now)
        self.assertEqual(entry.sha256, hashlib.sha256(current).hexdigest())

    def test_execute_requires_backup_and_paused_writer_confirmations(self):
        name = self.private_photo_path("protected.jpg")
        content = b"protected photo"
        photo = self.write_photo(name, content)
        self.inventory_photo(name, content, days_ago=100)

        with self.assertRaisesMessage(CommandError, "--confirm-no-writers"):
            self.run_command("--execute")
        with self.assertRaisesMessage(CommandError, "--confirm-backup"):
            self.run_command("--execute", "--confirm-no-writers")

        self.assertTrue(photo.exists())
        self.assertTrue(
            StudentPhotoRetentionInventory.objects.filter(path=name).exists()
        )

    def test_unsafe_storage_path_aborts_inventory_without_changes(self):
        name = self.private_photo_path("unsafe.jpg")
        photo = self.write_photo(name)

        with patch.object(
            cleanup_student_photos,
            "_scan_private_photos",
            return_value=({}, 1),
        ):
            with self.assertRaisesMessage(CommandError, "no inventory changes"):
                self.run_command("--inventory")

        self.assertTrue(photo.exists())
        self.assertFalse(StudentPhotoRetentionInventory.objects.exists())

    def test_unknown_organization_directory_aborts_inventory(self):
        name = "private/student_photos/999999/unknown-org.jpg"
        photo = self.write_photo(name)

        with self.assertRaisesMessage(CommandError, "no inventory changes"):
            self.run_command("--inventory")

        self.assertTrue(photo.exists())
        self.assertFalse(StudentPhotoRetentionInventory.objects.exists())

    def test_legacy_photos_and_signed_documents_are_outside_cleanup_scope(self):
        legacy_photo = self.write_photo("photos/students/legacy.jpg")
        signed_document = self.write_photo(
            "private/application_documents/submission.pdf", b"signed document"
        )

        self.run_command("--inventory")

        self.assertTrue(legacy_photo.exists())
        self.assertTrue(signed_document.exists())
        self.assertFalse(StudentPhotoRetentionInventory.objects.exists())
