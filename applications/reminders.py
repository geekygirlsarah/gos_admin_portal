"""Tenant-scoped application reminder selection and delivery."""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Exists, OuterRef, Q, Value
from django.db.models.functions import Coalesce
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from organizations.utils import get_organization_email_settings
from programs.models import Program, ProgramDocument
from programs.utils.email_history import send_messages_with_history
from programs.utils.notifications import _mark_delivery_tracking

from .models import (
    Application,
    ApplicationDocumentSubmission,
    ApplicationReminderDelivery,
)
from .services import (
    _application_email_url,
    _from_email,
    document_recipient,
    has_required_application_documents,
    required_application_documents_complete,
)

logger = logging.getLogger(__name__)

INACTIVITY_DELAY = timedelta(hours=48)
DEADLINE_WINDOW = timedelta(days=2)
MAX_BATCH_SIZE = 1000

_DRAFT_STATUSES = (
    Application.Status.DRAFT,
    Application.Status.EMAIL_VERIFIED,
    Application.Status.AWAITING_PARENT,
)
_APPLICANT_TYPES = (
    Application.Type.STUDENT,
    Application.Type.PARENT,
    Application.Type.MENTOR,
)


@dataclass(frozen=True)
class ReminderPlan:
    phase: str
    reminder_types: tuple[str, ...]
    recipient: str
    application_url: str
    organization_name: str
    program_name: str
    deadline: object


@dataclass(frozen=True)
class ReminderClaim:
    application_pk: int
    phase: str
    reminder_types: tuple[str, ...]
    delivery_ids: tuple[int, ...]
    recipient: str
    application_url: str


def _program_is_open(program: Program, today) -> bool:
    """Use the same inclusive date rules as ``Program.is_applications_open``."""
    if not program.active:
        return False
    if program.applications_open and program.applications_open > today:
        return False
    if program.applications_close and program.applications_close < today:
        return False
    if program.end_date and program.end_date < today:
        return False
    return True


def _phase_for(application: Application) -> str | None:
    if application.status in _DRAFT_STATUSES:
        return ApplicationReminderDelivery.Phase.DRAFT
    if (
        application.status == Application.Status.SUBMITTED
        and application.applicant_type
        in (Application.Type.STUDENT, Application.Type.PARENT)
        and application.program_id
        and has_required_application_documents(application)
        and not required_application_documents_complete(application)
    ):
        return ApplicationReminderDelivery.Phase.MISSING_DOCUMENTS
    return None


def _due_reminder_types(application, phase, program, now) -> tuple[str, ...]:
    due = []
    progress_at = application.applicant_progress_at or application.updated_at
    if progress_at and now - progress_at >= INACTIVITY_DELAY:
        due.append(ApplicationReminderDelivery.ReminderType.INACTIVITY)

    if program is not None and program.applications_close:
        days_until_close = (program.applications_close - now.date()).days
        if 0 <= days_until_close <= DEADLINE_WINDOW.days:
            due.append(ApplicationReminderDelivery.ReminderType.DEADLINE)
    return tuple(due)


def _valid_email(value) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    email = value.strip()
    try:
        validate_email(email)
    except ValidationError:
        return ""
    return email.lower()


def _recipient_and_url(application, phase) -> tuple[str, str]:
    if phase == ApplicationReminderDelivery.Phase.MISSING_DOCUMENTS:
        recipient = _valid_email(document_recipient(application))
        path = reverse("apply_step10", kwargs={"app_id": application.application_id})
    elif (
        application.applicant_type == Application.Type.STUDENT
        and application.status == Application.Status.AWAITING_PARENT
    ):
        handoff = (application.data or {}).get("step7_handoff") or {}
        if not isinstance(handoff, dict) or not application.handoff_token:
            return "", ""
        recipient = _valid_email(handoff.get("parent_email"))
        if not recipient:
            return "", ""
        path = reverse(
            "apply_resume_link_with_token",
            kwargs={
                "app_id": application.application_id,
                "token": application.handoff_token,
            },
        )
    else:
        if not application.email_verified_at:
            return "", ""
        recipient = _valid_email(application.email)
        path = reverse(
            "apply_resume_link", kwargs={"app_id": application.application_id}
        )

    return recipient, _application_email_url(application, path)


def _make_plan(application, organization, program, now) -> ReminderPlan | None:
    if (
        not organization.is_active
        or application.organization_id != organization.pk
        or application.superseded_at is not None
        or application.applicant_type not in _APPLICANT_TYPES
        or not isinstance(application.data, dict)
    ):
        return None
    if application.program_id and (
        program is None
        or program.pk != application.program_id
        or program.organization_id != organization.pk
        or not _program_is_open(program, now.date())
    ):
        return None

    phase = _phase_for(application)
    if phase is None:
        return None
    reminder_types = _due_reminder_types(application, phase, program, now)
    if not reminder_types:
        return None
    recipient, application_url = _recipient_and_url(application, phase)
    if not recipient or not application_url:
        return None

    return ReminderPlan(
        phase=phase,
        reminder_types=reminder_types,
        recipient=recipient,
        application_url=application_url,
        organization_name=organization.name,
        program_name=program.name if program is not None else "",
        deadline=program.applications_close if program is not None else None,
    )


def _load_program(application, organization, using, *, lock=False):
    if not application.program_id:
        return None
    queryset = Program.objects.using(using).filter(
        pk=application.program_id,
        organization_id=organization.pk,
    )
    if lock:
        queryset = queryset.select_for_update()
    return queryset.first()


def _application_query(organization, using):
    return (
        Application.objects.using(using)
        .for_organization(organization)
        .filter(organization_id=organization.pk)
    )


def _candidate_queryset(organization, now):
    today = now.date()
    inactivity_due = Q(_reminder_progress__lte=now - INACTIVITY_DELAY)
    deadline_due = Q(
        program__applications_close__gte=today,
        program__applications_close__lte=today + DEADLINE_WINDOW,
    )
    program_window_open = Q(program__isnull=True) | (
        Q(program__organization_id=organization.pk, program__active=True)
        & (
            Q(program__applications_open__isnull=True)
            | Q(program__applications_open__lte=today)
        )
        & (
            Q(program__applications_close__isnull=True)
            | Q(program__applications_close__gte=today)
        )
        & (Q(program__end_date__isnull=True) | Q(program__end_date__gte=today))
    )
    verified_applicant = Q(email_verified_at__isnull=False, email__gt="")
    handed_off_parent = Q(
        status=Application.Status.AWAITING_PARENT,
        handoff_token__gt=Value(""),
        data__step7_handoff__parent_email__gt="",
    )
    guardian_recipient = (
        Q(applicant_type=Application.Type.PARENT, email__gt="")
        | Q(
            applicant_type=Application.Type.STUDENT,
            data__step7_handoff__parent_email__gt="",
        )
        | Q(
            applicant_type=Application.Type.STUDENT,
            **{"data__step7-primaryparent__email__gt": ""},
        )
        | Q(applicant_type=Application.Type.STUDENT, data__step7__email__gt="")
    )

    submitted_upload = ApplicationDocumentSubmission.objects.filter(
        application_id=OuterRef(OuterRef("pk")),
        organization_id=OuterRef(OuterRef("organization_id")),
        document_id=OuterRef("pk"),
    ).exclude(file="")
    missing_required_documents = (
        ProgramDocument.objects.filter(
            program_id=OuterRef("program_id"),
            program__organization_id=OuterRef("organization_id"),
            is_active=True,
            is_required=True,
        )
        .annotate(_has_current_upload=Exists(submitted_upload))
        .filter(_has_current_upload=False)
    )

    blocked_statuses = (
        ApplicationReminderDelivery.Status.PENDING,
        ApplicationReminderDelivery.Status.ACCEPTED,
        ApplicationReminderDelivery.Status.UNKNOWN,
    )

    def blocked_delivery(phase, reminder_type):
        return Exists(
            ApplicationReminderDelivery.objects.for_organization(organization).filter(
                application_id=OuterRef("pk"),
                organization_id=OuterRef("organization_id"),
                phase=phase,
                reminder_type=reminder_type,
                status__in=blocked_statuses,
            )
        )

    verified_recipient = verified_applicant & ~Q(
        applicant_type=Application.Type.STUDENT,
        status=Application.Status.AWAITING_PARENT,
    )
    draft_due = Q(status__in=_DRAFT_STATUSES) & (verified_recipient | handed_off_parent)
    draft_due &= (inactivity_due & ~Q(_draft_inactivity_blocked=True)) | (
        deadline_due & ~Q(_draft_deadline_blocked=True)
    )
    missing_documents_due = (
        Q(
            status=Application.Status.SUBMITTED,
            applicant_type__in=(Application.Type.STUDENT, Application.Type.PARENT),
            _has_missing_required_documents=True,
        )
        & guardian_recipient
    )
    missing_documents_due &= (
        inactivity_due & ~Q(_documents_inactivity_blocked=True)
    ) | (deadline_due & ~Q(_documents_deadline_blocked=True))

    return (
        _application_query(organization, "default")
        .filter(superseded_at__isnull=True)
        .annotate(
            _reminder_progress=Coalesce("applicant_progress_at", "updated_at"),
            _has_missing_required_documents=Exists(missing_required_documents),
            _draft_inactivity_blocked=blocked_delivery(
                ApplicationReminderDelivery.Phase.DRAFT,
                ApplicationReminderDelivery.ReminderType.INACTIVITY,
            ),
            _draft_deadline_blocked=blocked_delivery(
                ApplicationReminderDelivery.Phase.DRAFT,
                ApplicationReminderDelivery.ReminderType.DEADLINE,
            ),
            _documents_inactivity_blocked=blocked_delivery(
                ApplicationReminderDelivery.Phase.MISSING_DOCUMENTS,
                ApplicationReminderDelivery.ReminderType.INACTIVITY,
            ),
            _documents_deadline_blocked=blocked_delivery(
                ApplicationReminderDelivery.Phase.MISSING_DOCUMENTS,
                ApplicationReminderDelivery.ReminderType.DEADLINE,
            ),
        )
        .filter(program_window_open)
        .filter(draft_due | missing_documents_due)
        .order_by("_reminder_progress", "pk")
    )


def _available_types(organization, application, plan):
    existing = {
        reminder_type: status
        for reminder_type, status in ApplicationReminderDelivery.objects.for_organization(
            organization
        )
        .filter(
            application=application,
            phase=plan.phase,
        )
        .values_list("reminder_type", "status")
    }
    blocked = {
        ApplicationReminderDelivery.Status.PENDING,
        ApplicationReminderDelivery.Status.ACCEPTED,
        ApplicationReminderDelivery.Status.UNKNOWN,
    }
    return tuple(
        reminder_type
        for reminder_type in plan.reminder_types
        if existing.get(reminder_type) not in blocked
    )


def _read_plan(organization, application_pk, now):
    application = (
        _application_query(organization, "default")
        .select_related("program")
        .filter(pk=application_pk)
        .first()
    )
    if application is None:
        return None
    program = application.program
    if program is not None and program.organization_id != organization.pk:
        return None
    return _make_plan(application, organization, program, now)


def _claim_plan(organization, application_pk, now):
    using = "default"
    with transaction.atomic(using=using):
        application = (
            _application_query(organization, using)
            .select_for_update()
            .filter(pk=application_pk)
            .first()
        )
        if application is None:
            return None
        program = _load_program(application, organization, using, lock=True)
        plan = _make_plan(application, organization, program, now)
        if plan is None:
            return None

        delivery_ids = []
        claimed_types = []
        for reminder_type in plan.reminder_types:
            delivery = (
                ApplicationReminderDelivery.objects.using(using)
                .for_organization(organization)
                .select_for_update()
                .filter(
                    application=application,
                    organization_id=organization.pk,
                    phase=plan.phase,
                    reminder_type=reminder_type,
                )
                .first()
            )
            if delivery is None:
                delivery = ApplicationReminderDelivery.objects.using(using).create(
                    organization=organization,
                    application=application,
                    phase=plan.phase,
                    reminder_type=reminder_type,
                    status=ApplicationReminderDelivery.Status.PENDING,
                    claimed_at=now,
                    attempt_count=1,
                )
            elif delivery.status == ApplicationReminderDelivery.Status.FAILED:
                delivery.status = ApplicationReminderDelivery.Status.PENDING
                delivery.claimed_at = now
                delivery.completed_at = None
                delivery.attempt_count += 1
                delivery.save(
                    update_fields=[
                        "status",
                        "claimed_at",
                        "completed_at",
                        "attempt_count",
                        "updated_at",
                    ]
                )
            else:
                continue
            delivery_ids.append(delivery.pk)
            claimed_types.append(reminder_type)

        if not claimed_types:
            return None
        return ReminderClaim(
            application_pk=application.pk,
            phase=plan.phase,
            reminder_types=tuple(claimed_types),
            delivery_ids=tuple(delivery_ids),
            recipient=plan.recipient,
            application_url=plan.application_url,
        )


def _revalidate_claim(organization, claim, now):
    using = "default"
    with transaction.atomic(using=using):
        application = (
            _application_query(organization, using)
            .select_for_update()
            .filter(pk=claim.application_pk)
            .first()
        )
        if application is None:
            return None
        program = _load_program(application, organization, using, lock=True)
        plan = _make_plan(application, organization, program, now)
        if (
            plan is None
            or plan.phase != claim.phase
            or not set(claim.reminder_types).issubset(plan.reminder_types)
            or plan.recipient != claim.recipient
            or plan.application_url != claim.application_url
        ):
            return None
        pending_count = (
            ApplicationReminderDelivery.objects.using(using)
            .for_organization(organization)
            .filter(
                pk__in=claim.delivery_ids,
                application_id=claim.application_pk,
                organization_id=organization.pk,
                status=ApplicationReminderDelivery.Status.PENDING,
            )
            .count()
        )
        if pending_count != len(claim.delivery_ids):
            return None
        return replace(plan, reminder_types=claim.reminder_types)


def _finish_claim(organization, claim, status, now):
    ApplicationReminderDelivery.objects.using("default").for_organization(
        organization
    ).filter(
        pk__in=claim.delivery_ids,
        application_id=claim.application_pk,
        organization_id=organization.pk,
        status=ApplicationReminderDelivery.Status.PENDING,
    ).update(
        status=status, completed_at=now, updated_at=now
    )


def _build_message(organization, plan):
    inactivity_due = (
        ApplicationReminderDelivery.ReminderType.INACTIVITY in plan.reminder_types
    )
    deadline_due = (
        ApplicationReminderDelivery.ReminderType.DEADLINE in plan.reminder_types
    )
    missing_documents = (
        plan.phase == ApplicationReminderDelivery.Phase.MISSING_DOCUMENTS
    )
    context = {
        "organization_name": plan.organization_name,
        "program_name": plan.program_name,
        "application_url": plan.application_url,
        "deadline": plan.deadline,
        "inactivity_due": inactivity_due,
        "deadline_due": deadline_due,
        "missing_documents": missing_documents,
    }
    text_body = render_to_string("applications/email/application_reminder.txt", context)
    html_body = render_to_string(
        "applications/email/application_reminder.html", context
    )
    subject = (
        "Reminder: required application forms are incomplete"
        if missing_documents
        else "Reminder: continue your application"
    )
    email_settings = get_organization_email_settings(organization)
    reply_to = email_settings.get("reply_to_email") or getattr(
        settings, "DEFAULT_REPLY_TO_EMAIL", None
    )
    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=_from_email(organization),
        to=[plan.recipient],
        reply_to=[reply_to] if reply_to else None,
    )
    message.attach_alternative(html_body, "text/html")
    _mark_delivery_tracking(
        message,
        organization,
        history_category="application_reminder",
        retain_history_body=False,
    )
    return message


def _process_claim(organization, claim, now):
    plan = _revalidate_claim(organization, claim, now)
    if plan is None:
        _finish_claim(
            organization,
            claim,
            ApplicationReminderDelivery.Status.FAILED,
            now,
        )
        return {"failed": 1}

    try:
        message = _build_message(organization, plan)
    except Exception as exc:
        _finish_claim(
            organization,
            claim,
            ApplicationReminderDelivery.Status.FAILED,
            now,
        )
        logger.error(
            "Could not prepare an application reminder (error=%s).",
            type(exc).__name__,
        )
        return {"failed": 1}

    try:
        sent_count = send_messages_with_history([message])
    except Exception as exc:
        _finish_claim(
            organization,
            claim,
            ApplicationReminderDelivery.Status.UNKNOWN,
            now,
        )
        logger.error(
            "Application reminder delivery outcome is unknown (error=%s).",
            type(exc).__name__,
        )
        return {"unknown": 1}

    if sent_count == 1:
        status = ApplicationReminderDelivery.Status.ACCEPTED
        outcome = {"sent": 1}
    else:
        status = ApplicationReminderDelivery.Status.UNKNOWN
        outcome = {"unknown": 1}
    _finish_claim(organization, claim, status, timezone.now())
    return outcome


def _process_candidate(organization, application_pk, now, dry_run):
    if dry_run:
        plan = _read_plan(organization, application_pk, now)
        if plan is None:
            return {"skipped": 1}
        return (
            {"planned": 1}
            if _available_types(organization, application_pk, plan)
            else {}
        )

    try:
        claim = _claim_plan(organization, application_pk, now)
    except Exception as exc:
        logger.error(
            "Could not claim an application reminder (error=%s).",
            type(exc).__name__,
        )
        return {"failed": 1}
    if claim is None:
        return {"skipped": 1}

    outcome = {"planned": 1}
    outcome.update(_process_claim(organization, claim, now))
    return outcome


def run_application_reminder_batch(
    organization, *, now=None, dry_run=False, limit=200
) -> dict[str, int]:
    """Preview or deliver at most ``limit`` due application reminder messages."""
    if not 1 <= limit <= MAX_BATCH_SIZE:
        raise ValueError(f"limit must be between 1 and {MAX_BATCH_SIZE}.")
    now = now or timezone.now()
    summary = {"planned": 0, "sent": 0, "failed": 0, "unknown": 0, "skipped": 0}
    if organization is None or not organization.is_active:
        return summary

    application_ids = list(
        _candidate_queryset(organization, now).values_list("pk", flat=True)[:limit]
    )
    for application_pk in application_ids:
        outcome = _process_candidate(organization, application_pk, now, dry_run)
        for key, count in outcome.items():
            summary[key] += count

    return summary
