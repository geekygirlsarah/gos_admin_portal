"""Tenant-scoped travel reminder selection, claims, and delivery outcomes."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.db import transaction
from django.db.models import F, Prefetch, Q
from django.utils import timezone

from organizations.models import Organization
from programs.models import AdultStudentRelationship
from travel.models import (
    TravelEvent,
    TravelReminderDelivery,
    TravelSignedPermissionForm,
    TravelSignup,
)
from travel.services import _send_individually, trip_link
from travel.utils import parent_recipients, student_recipients

logger = logging.getLogger(__name__)

MAX_BATCH_SIZE = 1000
SIGNUP_REMINDER_DELAY = timedelta(days=3)
TRAVEL_REMINDER_DAYS = {
    TravelReminderDelivery.ReminderType.SEVEN_DAYS: 7,
    TravelReminderDelivery.ReminderType.ONE_DAY: 1,
}


@dataclass(frozen=True)
class ReminderPlan:
    signup_id: int
    target_date: object
    reminder_types: tuple[str, ...]
    reasons: tuple[str, ...]
    recipients: tuple[str, ...]


@dataclass(frozen=True)
class ReminderClaim:
    signup_id: int
    event_id: int
    target_date: object
    reminder_types: tuple[str, ...]
    delivery_ids: tuple[int, ...]
    recipients: tuple[str, ...]


def _organization_local_date(organization, now):
    timezone_name = organization.settings.get("time_zone", settings.TIME_ZONE)
    try:
        zone = ZoneInfo(timezone_name)
    except (TypeError, ValueError, ZoneInfoNotFoundError):
        logger.warning(
            "Invalid organization time zone for travel reminders "
            "(organization_id=%s); using the project time zone.",
            organization.pk,
        )
        zone = ZoneInfo(settings.TIME_ZONE)
    return timezone.localtime(now, zone).date()


def _signup_queryset(organization):
    relationships = (
        AdultStudentRelationship.objects.for_organization(organization)
        .filter(
            organization=organization,
            adult__organization=organization,
            student__organization=organization,
            adult__is_incomplete_application_profile=False,
            student__is_incomplete_application_profile=False,
        )
        .select_related("adult", "student")
    )
    signed_forms = (
        TravelSignedPermissionForm.objects.for_organization(organization)
        .filter(
            organization=organization,
            signup__organization=organization,
            signup__student__organization=organization,
        )
        .select_related("organization")
    )
    return (
        TravelSignup.objects.for_organization(organization)
        .filter(
            organization=organization,
            event__organization=organization,
            event__program__organization=organization,
            student__organization=organization,
            status__in=(TravelSignup.INTERESTED, TravelSignup.APPROVED),
        )
        .filter(
            Q(departure__isnull=True)
            | Q(
                departure__organization=organization,
                departure__event_id=F("event_id"),
            )
        )
        .select_related(
            "organization",
            "student__user",
            "event",
            "event__program",
            "departure",
        )
        .prefetch_related(
            Prefetch(
                "student__adultstudentrelationship_set",
                queryset=relationships,
                to_attr="tenant_contact_relationships",
            ),
            Prefetch(
                "signed_permission_forms",
                queryset=signed_forms,
                to_attr="reminder_signed_permission_forms",
            ),
        )
    )


def _prepare_signup(signup):
    signup.current_signed_permission_forms = [
        form
        for form in getattr(signup, "reminder_signed_permission_forms", ())
        if form.form_version == signup.event.permission_form_version and form.file
    ]
    return signup


def _reminder_reasons(signup):
    reasons = []
    if signup.status == TravelSignup.INTERESTED:
        reasons.append(
            "A parent or guardian still needs to approve the current cost and travel choices."
        )
    if (
        signup.event.requires_permission_form
        and signup.current_signed_permission_form is None
    ):
        reasons.append("A current signed permission form is still needed.")
    if signup.event.requires_departure and signup.departure_id is None:
        reasons.append("A departure choice is still needed.")
    return tuple(reasons)


def _recipients(signup, organization):
    result = {}
    for email in student_recipients(signup.student) + parent_recipients(
        signup.student, organization=organization
    ):
        email = email.strip()
        if email:
            result.setdefault(email.casefold(), email)
    return tuple(result.values())


def _make_plan(signup, organization, now):
    if (
        not organization.is_active
        or signup.organization_id != organization.pk
        or signup.event.organization_id != organization.pk
        or signup.event.program is None
        or signup.event.program.organization_id != organization.pk
        or signup.student.organization_id != organization.pk
        or signup.status not in (TravelSignup.INTERESTED, TravelSignup.APPROVED)
    ):
        return None
    if signup.departure_id and (
        signup.departure.organization_id != organization.pk
        or signup.departure.event_id != signup.event_id
    ):
        return None

    local_date = _organization_local_date(organization, now)
    if signup.event.end_date < local_date:
        return None
    target_date = (
        signup.departure.leave_date if signup.departure_id else signup.event.start_date
    )
    if target_date < local_date:
        return None

    reasons = _reminder_reasons(signup)
    if not reasons:
        return None

    reminder_types = []
    if (
        signup.status == TravelSignup.INTERESTED
        and signup.created_at
        and now >= signup.created_at + SIGNUP_REMINDER_DELAY
    ):
        reminder_types.append(TravelReminderDelivery.ReminderType.AFTER_SIGNUP)
    days_until_travel = (target_date - local_date).days
    for reminder_type, days in TRAVEL_REMINDER_DAYS.items():
        if days_until_travel == days:
            reminder_types.append(reminder_type)
    if not reminder_types:
        return None

    return ReminderPlan(
        signup_id=signup.pk,
        target_date=target_date,
        reminder_types=tuple(reminder_types),
        reasons=reasons,
        recipients=_recipients(signup, organization),
    )


def _candidate_queryset(organization, now):
    local_date = _organization_local_date(organization, now)
    travel_dates = (local_date + timedelta(days=7), local_date + timedelta(days=1))
    due = Q(status=TravelSignup.INTERESTED, created_at__lte=now - SIGNUP_REMINDER_DELAY)
    due |= Q(
        Q(departure__isnull=False, departure__leave_date__in=travel_dates)
        | Q(departure__isnull=True, event__start_date__in=travel_dates)
    )
    return (
        _signup_queryset(organization)
        .filter(event__end_date__gte=local_date)
        .filter(due)
        .order_by("created_at", "pk")
    )


def _row_target_date(signup, reminder_type, travel_target_date):
    if reminder_type == TravelReminderDelivery.ReminderType.AFTER_SIGNUP:
        return signup.created_at.date()
    return travel_target_date


def _available_types(organization, signup, plan):
    existing = {
        (row.reminder_type, row.target_date)
        for row in TravelReminderDelivery.objects.for_organization(organization).filter(
            organization=organization,
            signup=signup,
            reminder_type__in=plan.reminder_types,
        )
    }
    return tuple(
        reminder_type
        for reminder_type in plan.reminder_types
        if (
            reminder_type,
            _row_target_date(signup, reminder_type, plan.target_date),
        )
        not in existing
    )


def _read_plan(organization, signup_id, now):
    signup = _signup_queryset(organization).filter(pk=signup_id).first()
    if signup is None:
        return None, None
    _prepare_signup(signup)
    return signup, _make_plan(signup, organization, now)


def _locked_signup(organization, signup_id, using):
    event_id = (
        TravelSignup.objects.using(using)
        .for_organization(organization)
        .filter(
            pk=signup_id,
            organization=organization,
            student__organization=organization,
            event__organization=organization,
        )
        .values_list("event_id", flat=True)
        .first()
    )
    if event_id is None:
        return None
    event = (
        TravelEvent.objects.using(using)
        .for_organization(organization)
        .select_for_update()
        .filter(
            pk=event_id,
            organization=organization,
            program__organization=organization,
        )
        .first()
    )
    if event is None:
        return None
    signup = (
        _signup_queryset(organization)
        .using(using)
        .select_for_update(of=("self",))
        .filter(pk=signup_id, event_id=event.pk)
        .first()
    )
    if signup is None:
        return None
    _prepare_signup(signup)
    return signup


def _claim_plan(organization, signup_id, now):
    using = "default"
    with transaction.atomic(using=using):
        signup = _locked_signup(organization, signup_id, using)
        if signup is None:
            return None
        plan = _make_plan(signup, organization, now)
        if plan is None:
            return None

        delivery_ids = []
        claimed_types = []
        for reminder_type in plan.reminder_types:
            target_date = _row_target_date(signup, reminder_type, plan.target_date)
            delivery = (
                TravelReminderDelivery.objects.using(using)
                .for_organization(organization)
                .select_for_update()
                .filter(
                    organization=organization,
                    signup=signup,
                    reminder_type=reminder_type,
                    target_date=target_date,
                )
                .first()
            )
            if delivery is not None:
                continue
            delivery = TravelReminderDelivery.objects.using(using).create(
                organization=organization,
                signup=signup,
                reminder_type=reminder_type,
                target_date=target_date,
                status=TravelReminderDelivery.Status.PENDING,
                claimed_at=now,
            )
            delivery_ids.append(delivery.pk)
            claimed_types.append(reminder_type)

        if not claimed_types:
            return None
        return ReminderClaim(
            signup_id=signup.pk,
            event_id=signup.event_id,
            target_date=plan.target_date,
            reminder_types=tuple(claimed_types),
            delivery_ids=tuple(delivery_ids),
            recipients=plan.recipients,
        )


def _revalidate_claim(organization, claim, now):
    using = "default"
    with transaction.atomic(using=using):
        signup = _locked_signup(organization, claim.signup_id, using)
        if signup is None or signup.event_id != claim.event_id:
            return None, None
        plan = _make_plan(signup, organization, now)
        if (
            plan is None
            or plan.target_date != claim.target_date
            or not set(claim.reminder_types).issubset(plan.reminder_types)
            or plan.recipients != claim.recipients
        ):
            return None, None
        pending_count = (
            TravelReminderDelivery.objects.using(using)
            .for_organization(organization)
            .filter(
                pk__in=claim.delivery_ids,
                signup_id=claim.signup_id,
                organization=organization,
                status=TravelReminderDelivery.Status.PENDING,
            )
            .count()
        )
        if pending_count != len(claim.delivery_ids):
            return None, None
        return signup, plan


def _finish_claim(
    organization, claim, status, now, recipient_count=0, accepted_count=0
):
    TravelReminderDelivery.objects.for_organization(organization).filter(
        pk__in=claim.delivery_ids,
        signup_id=claim.signup_id,
        organization=organization,
        status=TravelReminderDelivery.Status.PENDING,
    ).update(
        status=status,
        completed_at=now,
        recipient_count=recipient_count,
        accepted_count=accepted_count,
        updated_at=now,
    )


def _process_claim(organization, claim, now):
    signup, plan = _revalidate_claim(organization, claim, now)
    if plan is None:
        _finish_claim(
            organization,
            claim,
            TravelReminderDelivery.Status.FAILED,
            timezone.now(),
        )
        return {"failed": 1}
    if not plan.recipients:
        _finish_claim(
            organization,
            claim,
            TravelReminderDelivery.Status.NO_RECIPIENTS,
            timezone.now(),
        )
        return {"no_recipients": 1}

    try:
        accepted_count, recipient_count = _send_individually(
            f"Travel: Action needed for {signup.event.name}",
            "travel/emails/signup_reminder.html",
            {
                "event": signup.event,
                "student": signup.student,
                "reasons": plan.reasons,
                "trip_link": trip_link(signup.event),
            },
            plan.recipients,
            organization,
        )
    except Exception as exc:
        logger.error(
            "Could not prepare a travel reminder (signup_id=%s error=%s).",
            signup.pk,
            type(exc).__name__,
        )
        _finish_claim(
            organization,
            claim,
            TravelReminderDelivery.Status.FAILED,
            timezone.now(),
            len(plan.recipients),
        )
        return {"failed": 1}

    if accepted_count == recipient_count:
        status = TravelReminderDelivery.Status.ACCEPTED
        outcome = {"sent": 1}
    else:
        status = TravelReminderDelivery.Status.UNKNOWN
        outcome = {"unknown": 1}
    _finish_claim(
        organization,
        claim,
        status,
        timezone.now(),
        recipient_count,
        accepted_count,
    )
    return outcome


def _process_candidate(organization, signup_id, now, dry_run):
    if dry_run:
        signup, plan = _read_plan(organization, signup_id, now)
        if signup is None or plan is None:
            return {"skipped": 1}
        return {"planned": 1} if _available_types(organization, signup, plan) else {}

    try:
        claim = _claim_plan(organization, signup_id, now)
    except Exception as exc:
        logger.error(
            "Could not claim a travel reminder (signup_id=%s error=%s).",
            signup_id,
            type(exc).__name__,
        )
        return {"failed": 1}
    if claim is None:
        return {"skipped": 1}
    outcome = {"planned": 1}
    outcome.update(_process_claim(organization, claim, now))
    return outcome


def run_travel_reminder_batch(organization, *, now=None, dry_run=False, limit=200):
    """Preview or deliver at most ``limit`` due travel reminder messages."""
    if not 1 <= limit <= MAX_BATCH_SIZE:
        raise ValueError(f"limit must be between 1 and {MAX_BATCH_SIZE}.")
    now = now or timezone.now()
    summary = {
        "planned": 0,
        "sent": 0,
        "failed": 0,
        "unknown": 0,
        "no_recipients": 0,
        "skipped": 0,
    }
    if organization is None or not organization.is_active:
        return summary

    signup_ids = list(
        _candidate_queryset(organization, now).values_list("pk", flat=True)[:limit]
    )
    for signup_id in signup_ids:
        outcome = _process_candidate(organization, signup_id, now, dry_run)
        for key, count in outcome.items():
            summary[key] += count
    return summary
