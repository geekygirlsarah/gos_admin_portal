"""Move legacy student photos and signed application files into private storage.

The command is read-only unless ``--execute`` is supplied. It never removes
legacy sources; interrupted copies that are not referenced in the database
must be reviewed separately.
"""

import dataclasses
import hashlib
import os
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from django.conf import settings
from django.core.exceptions import SuspiciousFileOperation
from django.core.files import File
from django.core.files.storage import FileSystemStorage
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from applications.models import ApplicationDocumentSubmission
from programs.models import Student


class TransitionReviewError(Exception):
    """The record or its file cannot be safely transitioned automatically."""


@dataclass
class CategoryCounts:
    would_transition: int = 0
    transitioned: int = 0
    skipped: int = 0
    review: int = 0
    review_reasons: Counter[str] = dataclasses.field(default_factory=Counter)


def _is_within(path, parent):
    try:
        return os.path.commonpath((str(path), str(parent))) == str(parent)
    except ValueError:
        return False


def _validate_relative_name(name):
    if not name or "\\" in name:
        raise TransitionReviewError("Invalid storage path")
    relative = PurePosixPath(name)
    if (
        relative.is_absolute()
        or relative.as_posix() != name
        or any(part in ("", ".", "..") for part in relative.parts)
    ):
        raise TransitionReviewError("Invalid storage path")
    return relative


def _storage_and_root(field):
    storage = field.storage
    if not isinstance(storage, FileSystemStorage):
        raise TransitionReviewError("Unsupported file storage")

    try:
        configured_root = Path(settings.MEDIA_ROOT).resolve()
        storage_root = Path(storage.location).resolve()
    except (OSError, TypeError, ValueError) as exc:
        raise TransitionReviewError("Invalid media storage root") from exc

    if storage_root != configured_root:
        raise TransitionReviewError("File storage is outside MEDIA_ROOT")
    return storage, storage_root


def _path_for_name(storage, root, name, prefix):
    _validate_relative_name(name)
    normalized_prefix = prefix.rstrip("/") + "/"
    if not name.startswith(normalized_prefix):
        raise TransitionReviewError("File is outside its expected directory")

    try:
        candidate = Path(storage.path(name))
        candidate.relative_to(root)
    except (OSError, ValueError, SuspiciousFileOperation) as exc:
        raise TransitionReviewError("File path is outside MEDIA_ROOT") from exc
    return candidate


def _reject_symlink_components(root, candidate):
    current = root
    for part in candidate.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            raise TransitionReviewError("Symbolic links are not supported")


def _existing_path(storage, root, name, prefix):
    candidate = _path_for_name(storage, root, name, prefix)
    _reject_symlink_components(root, candidate)
    try:
        resolved = candidate.resolve(strict=True)
        expected_directory = (
            root / PurePosixPath(prefix.rstrip("/")).as_posix()
        ).resolve(strict=True)
    except OSError as exc:
        raise TransitionReviewError("File is missing or unreadable") from exc
    if not _is_within(resolved, root) or not _is_within(resolved, expected_directory):
        raise TransitionReviewError("File resolves outside its expected directory")
    if not resolved.is_file():
        raise TransitionReviewError("Stored path is not a regular file")
    return resolved


def _destination_path(storage, root, name, prefix):
    candidate = _path_for_name(storage, root, name, prefix)
    _reject_symlink_components(root, candidate)
    if not _is_within(candidate, root):
        raise TransitionReviewError("Destination is outside MEDIA_ROOT")
    return candidate


def _validate_record_ownership(record, kind):
    if kind == "student_photo":
        if not record.organization_id:
            raise TransitionReviewError("Student has no organization")
        return

    application = record.application
    program = record.document.program
    organization_id = record.organization_id
    if not organization_id:
        raise TransitionReviewError("Submission has no organization")
    if (
        application.organization_id != organization_id
        or program.organization_id != organization_id
        or (
            application.program_id
            and application.program.organization_id != organization_id
        )
    ):
        raise TransitionReviewError("Submission ownership is inconsistent")


def _private_prefix(record, kind):
    if kind == "student_photo":
        return f"private/student_photos/{record.organization_id}/"
    return (
        f"private/application_documents/{record.organization_id}/"
        f"{record.application.application_id}/"
    )


def _sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_and_verify(
    record, field, storage, root, source_path, source_name, private_prefix
):
    extension = Path(source_name).suffix
    destination_name = field.generate_filename(
        record, f"transition{extension}"
    ).replace("\\", "/")
    if not destination_name.startswith(private_prefix):
        raise TransitionReviewError("Generated destination is outside private storage")
    _destination_path(
        storage,
        root,
        destination_name,
        private_prefix,
    )

    source_digest = _sha256_file(source_path)
    try:
        with source_path.open("rb") as source_file:
            saved_name = storage.save(destination_name, File(source_file))
    except (OSError, ValueError, SuspiciousFileOperation) as exc:
        raise TransitionReviewError("Private copy could not be created") from exc

    destination_path = _existing_path(
        storage,
        root,
        saved_name,
        private_prefix,
    )
    if _sha256_file(destination_path) != source_digest:
        raise TransitionReviewError("Private copy verification failed")
    return saved_name


class Command(BaseCommand):
    help = "Inventory or transition legacy student photos and signed application files."

    def add_arguments(self, parser):
        parser.add_argument(
            "--execute",
            action="store_true",
            help="Copy and verify files, then update database references (default: dry run).",
        )

    def handle(self, *args, **options):
        execute = options["execute"]
        counts = {
            "student photos": CategoryCounts(),
            "signed application documents": CategoryCounts(),
        }

        for record, kind, category, field_name, legacy_prefix in self._records():
            category_counts = counts[category]
            field = record._meta.get_field(field_name)
            try:
                name = getattr(record, field_name).name
                if not name:
                    continue

                _validate_record_ownership(record, kind)
                storage, root = _storage_and_root(field)
                private_prefix = _private_prefix(record, kind)
                private_root = (
                    "private/student_photos/"
                    if kind == "student_photo"
                    else "private/application_documents/"
                )
                if name.startswith(private_root):
                    _existing_path(
                        storage,
                        root,
                        name,
                        private_prefix,
                    )
                    category_counts.skipped += 1
                    continue

                _existing_path(
                    storage,
                    root,
                    name,
                    legacy_prefix,
                )
                if not execute:
                    category_counts.would_transition += 1
                    continue

                self._transition_record(
                    record,
                    kind,
                    field_name,
                    name,
                    legacy_prefix,
                )
                category_counts.transitioned += 1
            except (
                TransitionReviewError,
                OSError,
                TypeError,
                ValueError,
                SuspiciousFileOperation,
            ) as exc:
                category_counts.review += 1
                reason = (
                    str(exc)
                    if isinstance(exc, TransitionReviewError)
                    else type(exc).__name__
                )
                category_counts.review_reasons[reason] += 1

        mode = "execute" if execute else "dry run"
        self.stdout.write(f"Private media transition {mode} summary:")
        for category, category_counts in counts.items():
            self.stdout.write(
                f"{category}: would transition={category_counts.would_transition}, "
                f"transitioned={category_counts.transitioned}, "
                f"skipped={category_counts.skipped}, "
                f"needs review={category_counts.review}"
            )
            for reason, count in sorted(category_counts.review_reasons.items()):
                self.stdout.write(f"  {reason}: {count}")

        review_count = sum(
            category_counts.review for category_counts in counts.values()
        )
        if review_count:
            details = "; ".join(
                f"{category}: "
                + ", ".join(
                    f"{reason}={count}"
                    for reason, count in sorted(category_counts.review_reasons.items())
                )
                for category, category_counts in counts.items()
                if category_counts.review_reasons
            )
            raise CommandError(
                f"{review_count} file record(s) need manual review"
                f" ({details}); no source files were removed."
            )

    @staticmethod
    def _records():
        students = (
            Student._base_manager.exclude(photo__isnull=True)
            .exclude(photo="")
            .iterator(chunk_size=200)
        )
        for student in students:
            yield student, "student_photo", "student photos", "photo", "photos/students/"

        submissions = (
            ApplicationDocumentSubmission._base_manager.select_related(
                "application__program",
                "document__program",
            )
            .exclude(file__isnull=True)
            .exclude(file="")
            .iterator(chunk_size=200)
        )
        for submission in submissions:
            yield (
                submission,
                "application_document",
                "signed application documents",
                "file",
                "application_documents/",
            )

    @staticmethod
    def _transition_record(record, kind, field_name, original_name, legacy_prefix):
        model = record.__class__
        field = model._meta.get_field(field_name)
        ownership_relations = (
            ("application__program", "document__program")
            if kind == "application_document"
            else ()
        )
        with transaction.atomic():
            queryset = model._base_manager.select_for_update()
            if ownership_relations:
                queryset = queryset.select_related(*ownership_relations)
            locked = queryset.get(pk=record.pk)
            if getattr(locked, field_name).name != original_name:
                raise TransitionReviewError("Record changed during transition")
            _validate_record_ownership(locked, kind)
            storage, root = _storage_and_root(field)
            private_prefix = _private_prefix(locked, kind)
            source_path = _existing_path(
                storage,
                root,
                original_name,
                legacy_prefix,
            )
            destination_name = _copy_and_verify(
                locked,
                field,
                storage,
                root,
                source_path,
                original_name,
                private_prefix,
            )

            fresh_queryset = model._base_manager
            if ownership_relations:
                fresh_queryset = fresh_queryset.select_related(*ownership_relations)
            fresh = fresh_queryset.get(pk=record.pk)
            if getattr(fresh, field_name).name != original_name:
                changed = True
            else:
                _validate_record_ownership(fresh, kind)
                setattr(fresh, field_name, destination_name)
                fresh.save(update_fields=[field_name])
                changed = False

        if changed:
            raise TransitionReviewError("Record changed during transition")
