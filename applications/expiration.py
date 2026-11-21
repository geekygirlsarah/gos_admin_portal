"""Organization-scoped expiration for unfinished applications."""

from __future__ import annotations

import logging
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from applications.models import Application, ApplicationDocumentSubmission
from applications.services import (
    has_required_application_documents,
    required_application_documents_complete,
)
from audit.service import log_deletion
from programs.models import Program

logger = logging.getLogger(__name__)

TIMEOUT_DAYS = 30
MAX_BATCH_SIZE = 500


class UnsafeApplicationExpiration(Exception):
    """The application or its files have inconsistent ownership."""


def _candidate_queryset(organization, cutoff):
    return (
        Application.objects.filter(
            organization_id=organization.pk,
            status__in=(
                Application.Status.DRAFT,
                Application.Status.EMAIL_VERIFIED,
                Application.Status.AWAITING_PARENT,
                Application.Status.SUBMITTED,
            ),
        )
        .filter(
            Q(applicant_progress_at__lte=cutoff)
            | Q(applicant_progress_at__isnull=True, updated_at__lte=cutoff)
        )
        .order_by("applicant_progress_at", "updated_at", "pk")
    )


def _check_program_ownership(application):
    if (
        application.program_id
        and not Program.objects.filter(
            pk=application.program_id,
            organization_id=application.organization_id,
        ).exists()
    ):
        raise UnsafeApplicationExpiration("program ownership is inconsistent")


def _is_expirable(application):
    if application.status in (
        Application.Status.DRAFT,
        Application.Status.EMAIL_VERIFIED,
    ):
        return True
    if application.status == Application.Status.AWAITING_PARENT:
        return application.applicant_type == Application.Type.STUDENT
    if (
        application.status == Application.Status.SUBMITTED
        and application.applicant_type
        in (Application.Type.STUDENT, Application.Type.PARENT)
        and has_required_application_documents(application)
    ):
        return not required_application_documents_complete(application)
    return False


def _application_storage_directory(application):
    return (
        f"private/application_documents/{application.organization_id}/"
        f"{application.application_id}"
    )


def _is_owned_storage_name(name, directory):
    if not isinstance(name, str) or not name or "\\" in name:
        return False
    parts = name.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return False
    return name.startswith(f"{directory}/")


def _owned_document_files(application):
    directory = _application_storage_directory(application)
    storage = ApplicationDocumentSubmission._meta.get_field("file").storage
    submissions = list(
        ApplicationDocumentSubmission.objects.filter(
            application_id=application.pk
        ).values(
            "organization_id",
            "document__program__organization_id",
            "file",
        )
    )
    file_names = set()
    for submission in submissions:
        if (
            submission["organization_id"] != application.organization_id
            or submission["document__program__organization_id"]
            != application.organization_id
        ):
            raise UnsafeApplicationExpiration("document ownership is inconsistent")
        name = submission["file"]
        if name:
            if not _is_owned_storage_name(name, directory):
                raise UnsafeApplicationExpiration(
                    "document path is not application-owned"
                )
            file_names.add(name)

    try:
        directories, filenames = storage.listdir(directory)
    except FileNotFoundError:
        directories, filenames = [], []
    except NotImplementedError as exc:
        raise UnsafeApplicationExpiration("storage listing is unavailable") from exc

    if directories:
        raise UnsafeApplicationExpiration("application document directory is not flat")
    for filename in filenames:
        if not isinstance(filename, str) or "/" in filename or "\\" in filename:
            raise UnsafeApplicationExpiration("storage returned an unsafe file name")
        name = f"{directory}/{filename}"
        if not _is_owned_storage_name(name, directory):
            raise UnsafeApplicationExpiration("storage returned an unsafe file path")
        file_names.add(name)

    return storage, sorted(file_names)


def _prepare_expiration(application):
    _check_program_ownership(application)
    if not _is_expirable(application):
        return None
    return _owned_document_files(application)


def _expire_locked(organization, application_pk, cutoff):
    with transaction.atomic():
        application = (
            Application.objects.select_for_update()
            .filter(pk=application_pk, organization_id=organization.pk)
            .first()
        )
        if application is None:
            return "skipped"

        progress_at = application.applicant_progress_at or application.updated_at
        if progress_at is None or progress_at > cutoff:
            return "skipped"

        prepared_files = _prepare_expiration(application)
        if prepared_files is None:
            return "skipped"
        storage, file_names = prepared_files

        audit_entry = log_deletion(
            resource=application,
            before={
                "application_id": application.application_id,
                "status": application.status,
            },
            notes="Application expired after 30 days without applicant progress.",
        )
        if audit_entry is None:
            raise RuntimeError("application deletion audit could not be recorded")

        for name in file_names:
            storage.delete(name)
        application.delete()
        return "deleted"


def _dry_run_status(organization, application_pk, cutoff):
    application = Application.objects.filter(
        pk=application_pk,
        organization_id=organization.pk,
    ).first()
    if application is None:
        return "skipped"
    progress_at = application.applicant_progress_at or application.updated_at
    if progress_at is None or progress_at > cutoff:
        return "skipped"
    return "eligible" if _prepare_expiration(application) is not None else "skipped"


def run_application_expiration_batch(
    organization,
    *,
    now=None,
    execute=False,
    limit=200,
):
    """Count or delete eligible applications, scoped to one organization."""
    if not 1 <= limit <= MAX_BATCH_SIZE:
        raise ValueError(f"limit must be between 1 and {MAX_BATCH_SIZE}")

    observed_at = now or timezone.now()
    cutoff = observed_at - timedelta(days=TIMEOUT_DAYS)
    application_ids = list(
        _candidate_queryset(organization, cutoff).values_list("pk", flat=True)[:limit]
    )
    result = {
        "processed": len(application_ids),
        "eligible": 0,
        "deleted": 0,
        "skipped": 0,
        "failed": 0,
    }

    for application_pk in application_ids:
        if not execute:
            try:
                state = _dry_run_status(organization, application_pk, cutoff)
                if state == "eligible":
                    result["eligible"] += 1
                else:
                    result["skipped"] += 1
            except Exception as exc:
                logger.error(
                    "Application expiration dry-run failed (error=%s)",
                    type(exc).__name__,
                )
                result["failed"] += 1
            continue

        try:
            state = _expire_locked(organization, application_pk, cutoff)
            result[state] += 1
            if state == "deleted":
                result["eligible"] += 1
        except Exception as exc:
            logger.error("Application expiration failed (error=%s)", type(exc).__name__)
            result["failed"] += 1

    return result
