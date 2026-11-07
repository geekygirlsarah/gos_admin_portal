"""Inventory and remove unreferenced private student photos after 90 days."""

import hashlib
import os
import stat
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path, PurePosixPath

from django.apps import apps
from django.core.exceptions import SuspiciousFileOperation
from django.core.management.base import BaseCommand, CommandError
from django.db import models, transaction
from django.utils import timezone
from pghistory.core import event_models

from organizations.models import Organization
from programs.management.commands.transition_private_media import (
    TransitionReviewError,
    _existing_path,
    _reject_symlink_components,
    _storage_and_root,
)
from programs.models import Adult, Student, StudentPhotoRetentionInventory

PRIVATE_PREFIX = "private/student_photos/"
RETENTION_PERIOD = timedelta(days=90)
CHUNK_SIZE = 200


@dataclass(frozen=True)
class FileIdentity:
    size: int
    sha256: str


def _photo_history_models():
    history_models = []
    for tracked_model in (Student, Adult):
        tracked_events = event_models(tracks_model=tracked_model)
        if not tracked_events:
            raise CommandError(
                "Student and adult photo history is unavailable; no files were changed."
            )
        for event_model in tracked_events:
            fields = {field.name for field in event_model._meta.fields}
            if not {"photo", "pgh_created_at"}.issubset(fields):
                raise CommandError(
                    "Student and adult photo history is incomplete; no files were changed."
                )
            history_models.append(event_model)
    return tuple(history_models)


def _all_history_models():
    return tuple(event_models())


def _organization_ids_for_paths(paths):
    organization_by_path = {}
    invalid_count = 0
    organization_ids = set()

    for name in paths:
        parts = PurePosixPath(name).parts
        if (
            len(parts) != 4
            or parts[:2] != ("private", "student_photos")
            or not parts[2].isdecimal()
        ):
            invalid_count += 1
            continue
        organization_id = int(parts[2])
        organization_by_path[name] = organization_id
        organization_ids.add(organization_id)

    known_organization_ids = set()
    organization_ids = sorted(organization_ids)
    for offset in range(0, len(organization_ids), CHUNK_SIZE):
        organization_batch = organization_ids[slice(offset, offset + CHUNK_SIZE)]
        known_organization_ids.update(
            Organization._base_manager.filter(pk__in=organization_batch).values_list(
                "pk", flat=True
            )
        )

    for name, organization_id in list(organization_by_path.items()):
        if organization_id not in known_organization_ids:
            del organization_by_path[name]
            invalid_count += 1

    return organization_by_path, invalid_count


def _current_photo_references(candidate_paths, history_models):
    if not candidate_paths:
        return set()

    history_models = set(history_models)
    references = set()
    for model in apps.get_models():
        if model in history_models or model._meta.proxy:
            continue
        for field in model._meta.get_fields():
            if not isinstance(field, models.FileField) or field.auto_created:
                continue
            names = (
                model._base_manager.filter(
                    **{f"{field.name}__startswith": PRIVATE_PREFIX}
                )
                .values_list(field.name, flat=True)
                .iterator(chunk_size=CHUNK_SIZE)
            )
            references.update(name for name in names if name in candidate_paths)
    return references


def _has_current_photo_reference(path, history_models):
    history_models = set(history_models)
    for model in apps.get_models():
        if model in history_models or model._meta.proxy:
            continue
        for field in model._meta.get_fields():
            if isinstance(field, models.FileField) and not field.auto_created:
                if model._base_manager.filter(**{field.name: path}).exists():
                    return True
    return False


def _paths_reused_after(inventory_by_path, history_models):
    if not inventory_by_path:
        return set()

    earliest_inventory = min(
        item.first_unreferenced_at for item in inventory_by_path.values()
    )
    reused_paths = set()
    for event_model in history_models:
        events = (
            event_model.objects.filter(
                photo__startswith=PRIVATE_PREFIX,
                pgh_created_at__gt=earliest_inventory,
            )
            .values_list("photo", "pgh_created_at")
            .iterator(chunk_size=CHUNK_SIZE)
        )
        for path, created_at in events:
            inventory_item = inventory_by_path.get(path)
            if inventory_item and created_at > inventory_item.first_unreferenced_at:
                reused_paths.add(path)
    return reused_paths


def _scan_private_photos(storage, root):
    directory = root / "private" / "student_photos"
    files = {}
    unsafe_count = 0

    try:
        _reject_symlink_components(root, directory)
    except (TransitionReviewError, ValueError):
        return files, 1

    if not directory.exists():
        return files, 0
    if not directory.is_dir():
        return files, 1

    pending_directories = [directory]
    while pending_directories:
        current_path = pending_directories.pop()
        try:
            entries = os.scandir(current_path)
        except OSError:
            unsafe_count += 1
            continue

        with entries:
            for entry in entries:
                child = Path(os.fsdecode(entry.path))
                try:
                    if entry.is_symlink():
                        unsafe_count += 1
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        pending_directories.append(child)
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        unsafe_count += 1
                        continue
                    name = child.relative_to(root).as_posix()
                    _existing_path(storage, root, name, PRIVATE_PREFIX)
                except (
                    TransitionReviewError,
                    OSError,
                    ValueError,
                    SuspiciousFileOperation,
                ):
                    unsafe_count += 1
                    continue
                files[name] = child

    return files, unsafe_count


def _file_identity(storage, root, name):
    path = _existing_path(storage, root, name, PRIVATE_PREFIX)
    with path.open("rb") as file_obj:
        before = os.fstat(file_obj.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise TransitionReviewError("Stored path is not a regular file")

        digest = hashlib.sha256()
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
        after = os.fstat(file_obj.fileno())

    if (before.st_size, before.st_mtime_ns, before.st_ino) != (
        after.st_size,
        after.st_mtime_ns,
        after.st_ino,
    ):
        raise TransitionReviewError("File changed while being inspected")
    return FileIdentity(size=after.st_size, sha256=digest.hexdigest())


class Command(BaseCommand):
    help = "Inventory or remove unreferenced private student photos after 90 days."

    def add_arguments(self, parser):
        actions = parser.add_mutually_exclusive_group()
        actions.add_argument(
            "--inventory",
            action="store_true",
            help="Record the first observation time for unreferenced private photos.",
        )
        actions.add_argument(
            "--execute",
            action="store_true",
            help="Delete previously inventoried, unchanged photos past 90 days.",
        )
        parser.add_argument(
            "--confirm-no-writers",
            action="store_true",
            help="Confirm application and background writers are paused (with --execute).",
        )
        parser.add_argument(
            "--confirm-backup",
            action="store_true",
            help="Confirm verified database and media backups exist (with --execute).",
        )

    def handle(self, *args, **options):
        execute = options["execute"]
        inventory = options["inventory"]
        if execute and not options["confirm_no_writers"]:
            raise CommandError("--execute requires --confirm-no-writers.")
        if execute and not options["confirm_backup"]:
            raise CommandError("--execute requires --confirm-backup.")

        storage, root = self._storage()
        history_models = _photo_history_models()
        all_history_models = _all_history_models()
        files, unsafe_count = _scan_private_photos(storage, root)
        organization_by_path, organization_issue_count = _organization_ids_for_paths(
            files
        )
        unsafe_count += organization_issue_count
        candidate_paths = set(organization_by_path)
        current_references = _current_photo_references(
            candidate_paths, all_history_models
        )
        unreferenced_paths = candidate_paths - current_references
        identities = {}
        for name in unreferenced_paths:
            try:
                identities[name] = _file_identity(storage, root, name)
            except (
                TransitionReviewError,
                OSError,
                ValueError,
                SuspiciousFileOperation,
            ):
                unsafe_count += 1

        inventory_items = list(
            StudentPhotoRetentionInventory._base_manager.select_related("organization")
            .filter(path__startswith=PRIVATE_PREFIX)
            .iterator(chunk_size=CHUNK_SIZE)
        )
        inventory_by_path = {item.path: item for item in inventory_items}
        for name in set(identities) & set(inventory_by_path):
            if inventory_by_path[name].organization.pk != organization_by_path[name]:
                identities.pop(name)
                unsafe_count += 1

        if (execute or inventory) and unsafe_count:
            raise CommandError(
                f"{unsafe_count} path(s) could not be verified safely; "
                "no inventory changes or file deletions were made."
            )

        reused_paths = _paths_reused_after(inventory_by_path, history_models)

        if inventory:
            self._record_inventory(
                identities,
                inventory_by_path,
                organization_by_path,
                current_references,
                reused_paths,
                timezone.now(),
            )
            return

        if execute:
            self._execute_cleanup(
                storage,
                root,
                identities,
                inventory_by_path,
                organization_by_path,
                current_references,
                reused_paths,
                history_models,
                all_history_models,
            )
            return

        self._preview(
            identities,
            inventory_by_path,
            current_references,
            reused_paths,
            unsafe_count,
            timezone.now(),
        )

    @staticmethod
    def _storage():
        try:
            return _storage_and_root(Student._meta.get_field("photo"))
        except (TransitionReviewError, OSError, TypeError, ValueError) as exc:
            raise CommandError(
                "Student photos are not stored in the supported MEDIA_ROOT filesystem."
            ) from exc

    def _record_inventory(
        self,
        identities,
        inventory_by_path,
        organization_by_path,
        current_references,
        reused_paths,
        now,
    ):
        newly_inventoried = 0
        restarted = 0
        unchanged = 0
        desired_paths = set(identities)

        with transaction.atomic():
            for name, identity in identities.items():
                existing = inventory_by_path.get(name)
                should_restart = existing is not None and (
                    existing.size != identity.size
                    or existing.sha256 != identity.sha256
                    or name in reused_paths
                )
                if existing is None:
                    StudentPhotoRetentionInventory._base_manager.create(
                        organization_id=organization_by_path[name],
                        path=name,
                        size=identity.size,
                        sha256=identity.sha256,
                        first_unreferenced_at=now,
                    )
                    newly_inventoried += 1
                elif should_restart:
                    existing.size = identity.size
                    existing.sha256 = identity.sha256
                    existing.first_unreferenced_at = now
                    existing.save(
                        update_fields=["size", "sha256", "first_unreferenced_at"]
                    )
                    restarted += 1
                else:
                    unchanged += 1

            obsolete_paths = set(inventory_by_path) - desired_paths
            if obsolete_paths:
                StudentPhotoRetentionInventory._base_manager.filter(
                    path__in=obsolete_paths
                ).delete()

        self.stdout.write("Student photo inventory update:")
        self.stdout.write(f"  newly inventoried={newly_inventoried}")
        self.stdout.write(f"  retention clock restarted={restarted}")
        self.stdout.write(f"  unchanged inventory entries={unchanged}")
        self.stdout.write(f"  currently referenced={len(current_references)}")
        self.stdout.write(f"  obsolete inventory entries removed={len(obsolete_paths)}")
        self.stdout.write("  no photo files were deleted")

    def _preview(
        self,
        identities,
        inventory_by_path,
        current_references,
        reused_paths,
        unsafe_count,
        now,
    ):
        counts = {
            "awaiting inventory": 0,
            "within 90-day retention": 0,
            "eligible for deletion": 0,
            "changed or reused; reinventory required": 0,
        }
        for name, identity in identities.items():
            item = inventory_by_path.get(name)
            if item is None:
                counts["awaiting inventory"] += 1
            elif (
                item.size != identity.size
                or item.sha256 != identity.sha256
                or name in reused_paths
            ):
                counts["changed or reused; reinventory required"] += 1
            elif now - item.first_unreferenced_at < RETENTION_PERIOD:
                counts["within 90-day retention"] += 1
            else:
                counts["eligible for deletion"] += 1

        self.stdout.write("Student photo cleanup dry run:")
        self.stdout.write(f"  unreferenced private photos={len(identities)}")
        self.stdout.write(f"  currently referenced={len(current_references)}")
        for label, count in counts.items():
            self.stdout.write(f"  {label}={count}")
        self.stdout.write(f"  unsafe or unreadable paths needing review={unsafe_count}")
        self.stdout.write("  no inventory records or photo files were changed")

    def _execute_cleanup(
        self,
        storage,
        root,
        identities,
        inventory_by_path,
        organization_by_path,
        current_references,
        reused_paths,
        history_models,
        all_history_models,
    ):
        deleted = 0
        protected = 0
        waiting = 0
        changed_or_reused = 0
        failed = 0
        now = timezone.now()

        obsolete_paths = (set(inventory_by_path) - set(identities)) | (
            set(inventory_by_path) & current_references
        )
        if obsolete_paths:
            StudentPhotoRetentionInventory._base_manager.filter(
                path__in=obsolete_paths
            ).delete()

        for name, identity in identities.items():
            item = inventory_by_path.get(name)
            if item is None:
                waiting += 1
                continue
            if name in reused_paths:
                changed_or_reused += 1
                continue
            if now - item.first_unreferenced_at < RETENTION_PERIOD:
                waiting += 1
                continue
            if item.size != identity.size or item.sha256 != identity.sha256:
                changed_or_reused += 1
                continue

            try:
                with transaction.atomic():
                    locked = (
                        StudentPhotoRetentionInventory._base_manager.select_related(
                            "organization"
                        )
                        .select_for_update(of=("self",))
                        .filter(pk=item.pk)
                        .first()
                    )
                    if locked is None:
                        waiting += 1
                        continue
                    if locked.organization.pk != organization_by_path[name]:
                        failed += 1
                        continue
                    is_referenced, is_reused = self._reference_status(
                        name, locked, history_models, all_history_models
                    )
                    if is_referenced:
                        locked.delete()
                        protected += 1
                        continue
                    if is_reused:
                        changed_or_reused += 1
                        continue
                    if timezone.now() - locked.first_unreferenced_at < RETENTION_PERIOD:
                        waiting += 1
                        continue
                    latest_identity = _file_identity(storage, root, name)
                    if (
                        latest_identity.size != locked.size
                        or latest_identity.sha256 != locked.sha256
                        or latest_identity != identity
                    ):
                        changed_or_reused += 1
                        continue
                    path = _existing_path(storage, root, name, PRIVATE_PREFIX)
                    path.unlink()
                    locked.delete()
                    deleted += 1
            except (
                TransitionReviewError,
                OSError,
                ValueError,
                SuspiciousFileOperation,
            ):
                failed += 1

        self.stdout.write("Student photo cleanup execute summary:")
        self.stdout.write(f"  deleted={deleted}")
        self.stdout.write(f"  currently referenced={protected}")
        self.stdout.write(f"  waiting for inventory or retention={waiting}")
        self.stdout.write(
            f"  changed or reused; reinventory required={changed_or_reused}"
        )
        self.stdout.write(f"  failed safety checks={failed}")
        if failed:
            raise CommandError(
                f"{failed} photo(s) could not be safely removed; inspect the "
                "filesystem and rerun the read-only preview."
            )

    @staticmethod
    def _reference_status(name, inventory_item, history_models, all_history_models):
        is_referenced = _has_current_photo_reference(name, all_history_models)
        is_reused = (
            False
            if is_referenced
            else name in _paths_reused_after({name: inventory_item}, history_models)
        )
        return is_referenced, is_reused
