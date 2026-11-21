import logging
from pathlib import Path

from django.core.exceptions import SuspiciousFileOperation
from django.core.files.storage import FileSystemStorage
from django.db import DEFAULT_DB_ALIAS, models, transaction
from django.db.models.deletion import ProtectedError
from django.db.models.signals import pre_delete
from django.dispatch import receiver

from programs.models import Program
from travel.models import (
    TravelEvent,
    TravelSignedFileDeletion,
    TravelSignedPermissionForm,
)

logger = logging.getLogger(__name__)


def _valid_signed_file_name(name, organization_id):
    parts = name.split("/")
    return (
        len(parts) == 5
        and parts[:4]
        == ["private", "travel", "signed_permissions", str(organization_id)]
        and parts[4] not in ("", ".", "..")
        and "\\" not in parts[4]
        and ":" not in parts[4]
        and not any(
            ord(character) < 32 or ord(character) == 127 for character in parts[4]
        )
    )


def _safe_storage_path(storage, name):
    if not isinstance(storage, FileSystemStorage):
        return True
    try:
        root = Path(storage.location).absolute()
        candidate = Path(storage.path(name))
        parts = candidate.relative_to(root).parts
        current = root
        for part in parts:
            current = current / part
            if current.is_symlink():
                return False
    except (OSError, ValueError, SuspiciousFileOperation):
        return False
    return True


def cleanup_signed_file(deletion_id, organization_id, using=DEFAULT_DB_ALIAS):
    with transaction.atomic(using=using):
        pending = (
            TravelSignedFileDeletion.objects.using(using)
            .select_for_update()
            .filter(pk=deletion_id, organization_id=organization_id)
            .first()
        )
        if pending is None:
            return True
        if not _valid_signed_file_name(pending.file_name, pending.organization_id):
            logger.warning("Signed travel file cleanup deferred: invalid private path.")
            return False
        if (
            TravelSignedPermissionForm._base_manager.using(using)
            .filter(file=pending.file_name)
            .exists()
        ):
            logger.warning(
                "Signed travel file cleanup deferred: file is still referenced."
            )
            return False
        storage = TravelSignedPermissionForm._meta.get_field("file").storage
        if not _safe_storage_path(storage, pending.file_name):
            logger.warning("Signed travel file cleanup deferred: unsafe storage path.")
            return False
        try:
            storage.delete(pending.file_name)
        except Exception:
            logger.warning("Signed travel file cleanup failed; retry is required.")
            return False
        pending.delete(using=using)
        return True


def _cleanup_after_commit(deletion_id, organization_id, using):
    try:
        cleanup_signed_file(deletion_id, organization_id, using=using)
    except Exception:
        logger.warning("Signed travel file cleanup failed; retry is required.")


@receiver(pre_delete, sender=TravelSignedPermissionForm)
def queue_signed_file_deletion(sender, instance, using, origin, **kwargs):
    allowed = (TravelEvent, Program)
    if not (
        isinstance(origin, allowed)
        or (isinstance(origin, models.QuerySet) and origin.model in allowed)
    ):
        raise ProtectedError(
            "Signed travel permission forms can only be deleted with their trip or program.",
            {instance},
        )
    signup = instance.signup
    if not (
        signup.organization_id == instance.organization_id
        and signup.student.organization_id == instance.organization_id
        and signup.event.organization_id == instance.organization_id
        and signup.event.program_id is not None
        and signup.event.program.organization_id == instance.organization_id
    ):
        raise ProtectedError(
            "Signed travel permission form ownership is inconsistent.", {instance}
        )
    if not instance.file.name:
        return
    pending, _ = TravelSignedFileDeletion.objects.using(using).get_or_create(
        file_name=instance.file.name,
        defaults={"organization_id": instance.organization_id},
    )
    if pending.organization_id != instance.organization_id:
        logger.warning("Signed travel file cleanup deferred: conflicting organization.")
        return
    transaction.on_commit(
        lambda: _cleanup_after_commit(pending.pk, instance.organization_id, using),
        using=using,
    )
