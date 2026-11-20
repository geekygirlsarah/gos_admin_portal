"""Status emails for the travel feature.

Every travel signup state transition emails the people it affects -- the
student (when they have a linked account) and/or their parents -- using
``send_templated_notification`` exactly like the fee/payment notifications in
``programs/signals.py``.
"""

import logging

from django.db.models import F, Prefetch, Q
from django.urls import reverse

from programs.models import AdultStudentRelationship
from programs.utils import send_templated_notification
from travel.models import (
    TravelEvent,
    TravelMentorSignup,
    TravelParentChaperoneSignup,
    TravelSignup,
)
from travel.utils import parent_recipients, student_recipients

logger = logging.getLogger(__name__)


def trip_link(event):
    return reverse("travel:event_list", kwargs={"program_id": event.program_id})


def _base_context(signup):
    event = signup.event
    return {
        "signup": signup,
        "event": event,
        "student": signup.student,
        "program": event.program,
        "trip_link": trip_link(event),
        "departure": signup.departure,
        "approved_total": signup.approved_total,
        "permission_form_available": event.requires_permission_form,
    }


def _send_individually(subject, template_name, context, recipients, organization):
    """Attempt each authorized recipient independently; never retry uncertain sends."""
    unique_recipients = {}
    for recipient in recipients:
        normalized = recipient.strip().casefold()
        if normalized:
            unique_recipients.setdefault(normalized, recipient.strip())

    accepted = 0
    for recipient_index, recipient in enumerate(unique_recipients.values(), start=1):
        try:
            send_templated_notification(
                subject,
                template_name,
                context,
                [recipient],
                organization=organization,
            )
        except Exception as exc:
            logger.warning(
                "Travel notification delivery outcome is uncertain "
                "(organization_id=%s recipient_index=%s error_type=%s).",
                organization.pk,
                recipient_index,
                type(exc).__name__,
            )
        else:
            accepted += 1
    return accepted, len(unique_recipients)


def _delivery_succeeded(result):
    if result is None:
        return False
    accepted, attempted = result
    return attempted > 0 and accepted == attempted


def _trusted_notification_organization(signup, profile):
    event = signup.event
    organization = getattr(signup, "organization", None)
    if organization is None or profile is None:
        return None
    if (
        event.organization_id != organization.pk
        or profile.organization_id != organization.pk
        or (event.program_id and event.program.organization_id != organization.pk)
        or (signup.departure_id and signup.departure.organization_id != organization.pk)
    ):
        return None
    return organization


def notify_signup_created(signup):
    """Student signed up (or a lead mentor added them): confirm to the student,
    ask the parents to review and approve."""
    organization = _trusted_notification_organization(signup, signup.student)
    if organization is None:
        return
    context = _base_context(signup)

    student_to = student_recipients(signup.student)
    results = []
    if student_to:
        results.append(
            _send_individually(
                f"Travel: You signed up for {signup.event.name}",
                "travel/emails/signup_created.html",
                context,
                student_to,
                organization,
            )
        )

    parent_to = parent_recipients(signup.student, organization=organization)
    if parent_to:
        results.append(
            _send_individually(
                f"Travel: {signup.student} wants to go to {signup.event.name} - please approve",
                "travel/emails/parent_approval_requested.html",
                context,
                parent_to,
                organization,
            )
        )
    else:
        results.append((0, 0))
    return (
        bool(parent_to)
        and bool(results)
        and all(
            accepted == attempted for accepted, attempted in results if attempted > 0
        )
    )


def notify_signup_approved(signup):
    organization = _trusted_notification_organization(signup, signup.student)
    if organization is None:
        return
    recipient = _family_notice_recipients(signup, organization)
    if not recipient:
        return
    context = _base_context(signup)
    return _delivery_succeeded(
        _send_individually(
            f"Travel: {signup.event.name} signup approved",
            "travel/emails/signup_approved.html",
            context,
            recipient,
            organization,
        )
    )


def notify_signup_declined(signup):
    organization = _trusted_notification_organization(signup, signup.student)
    if organization is None:
        return
    recipient = _family_notice_recipients(signup, organization)
    if not recipient:
        return
    context = _base_context(signup)
    return _delivery_succeeded(
        _send_individually(
            f"Travel: {signup.event.name} signup declined",
            "travel/emails/signup_declined.html",
            context,
            recipient,
            organization,
        )
    )


def notify_signup_withdrawn(signup):
    """Student (or a lead mentor for them) backed out: let the parents know and
    (when the student has an account) the student."""
    organization = _trusted_notification_organization(signup, signup.student)
    if organization is None:
        return
    context = _base_context(signup)
    parent_to = parent_recipients(signup.student, organization=organization)
    results = []
    if parent_to:
        results.append(
            _send_individually(
                f"Travel: {signup.student} is no longer going to {signup.event.name}",
                "travel/emails/signup_withdrawn.html",
                context,
                parent_to,
                organization,
            )
        )
    student_to = student_recipients(signup.student)
    if student_to:
        results.append(
            _send_individually(
                f"Travel: You are no longer going to {signup.event.name}",
                "travel/emails/signup_withdrawn.html",
                context,
                student_to,
                organization,
            )
        )
    return (
        bool(parent_to)
        and bool(results)
        and all(
            accepted == attempted for accepted, attempted in results if attempted > 0
        )
    )


def notify_signup_decision_undone(signup):
    """A parent rescinded an earlier approve/decline; it's back to 'interested'."""
    organization = _trusted_notification_organization(signup, signup.student)
    if organization is None:
        return False
    recipient = _family_notice_recipients(signup, organization)
    if not recipient:
        return False
    context = _base_context(signup)
    return _delivery_succeeded(
        _send_individually(
            f"Travel: {signup.event.name} decision updated",
            "travel/emails/signup_decision_undone.html",
            context,
            recipient,
            organization,
        )
    )


def notify_signed_permission_uploaded(signup_id, organization_id):
    signup, organization = _signup_for_family_notice(signup_id, organization_id)
    if signup is None:
        return False
    recipients = _family_notice_recipients(signup, organization)
    if not recipients:
        return False
    return _delivery_succeeded(
        _send_individually(
            f"Travel: Signed permission form received for {signup.event.name}",
            "travel/emails/signed_permission_uploaded.html",
            _base_context(signup),
            recipients,
            organization,
        )
    )


def build_trip_cancellation_notice(
    event, student_signups, mentor_signups, chaperone_signups
):
    organization = event.organization
    if event.program_id and event.program.organization_id != organization.pk:
        return None

    recipients = {}
    for signup in student_signups:
        if signup.status not in (TravelSignup.INTERESTED, TravelSignup.APPROVED):
            continue
        if _trusted_notification_organization(signup, signup.student) != organization:
            continue
        for email in _family_notice_recipients(signup, organization):
            recipients.setdefault(email.casefold(), email)

    for signup in (*mentor_signups, *chaperone_signups):
        if (
            not signup.adult.login_enabled
            or not signup.adult.email_updates
            or _trusted_notification_organization(signup, signup.adult) != organization
        ):
            continue
        email = (_adult_email(signup.adult) or "").strip()
        if email:
            recipients.setdefault(email.casefold(), email)

    return {
        "organization": organization,
        "event_name": event.name,
        "program_name": event.program.name if event.program_id else "",
        "start_date": event.start_date,
        "end_date": event.end_date,
        "location_name": event.location_name,
        "recipients": list(recipients.values()),
    }


def notify_trip_cancelled(notice):
    if not notice or not notice["recipients"]:
        return False
    context = {key: value for key, value in notice.items() if key != "recipients"}
    return _delivery_succeeded(
        _send_individually(
            f"Travel event cancelled: {notice['event_name']}",
            "travel/emails/event_cancelled.html",
            context,
            notice["recipients"],
            notice["organization"],
        )
    )


def _signup_for_family_notice(signup_id, organization_id):
    signup = (
        TravelSignup.objects.for_organization(organization_id)
        .filter(
            pk=signup_id,
            organization_id=organization_id,
            event__organization_id=organization_id,
            event__program__organization_id=organization_id,
            student__organization_id=organization_id,
            status__in=(TravelSignup.INTERESTED, TravelSignup.APPROVED),
        )
        .filter(
            Q(departure__isnull=True)
            | Q(
                departure__organization_id=organization_id,
                departure__event_id=F("event_id"),
            )
        )
        .select_related(
            "organization", "student__user", "event", "event__program", "departure"
        )
        .first()
    )
    if signup is None:
        return None, None
    organization = signup.organization
    if _trusted_notification_organization(signup, signup.student) != organization:
        return None, None
    return signup, organization


def _family_notice_recipients(signup, organization):
    recipients = student_recipients(signup.student) + parent_recipients(
        signup.student, organization=organization
    )
    unique_recipients = {}
    for email in recipients:
        normalized_email = email.strip().casefold()
        if normalized_email:
            unique_recipients.setdefault(normalized_email, email.strip())
    return list(unique_recipients.values())


def notify_signup_departure_changed(
    signup_id, organization_id, previous_departure, current_departure
):
    """Notify a student and their local guardians after a staff reassignment."""
    signup, organization = _signup_for_family_notice(signup_id, organization_id)
    if signup is None:
        return False
    recipients = _family_notice_recipients(signup, organization)
    if not recipients:
        return False
    context = {
        "event": signup.event,
        "program": signup.event.program,
        "student": signup.student,
        "previous_departure": previous_departure,
        "current_departure": current_departure,
        "trip_link": trip_link(signup.event),
    }
    return _delivery_succeeded(
        _send_individually(
            f"Travel: Departure changed for {signup.event.name}",
            "travel/emails/signup_departure_changed.html",
            context,
            recipients,
            organization,
        )
    )


def notify_signup_choices_updated(
    signup_id,
    organization_id,
    previous_departure,
    current_departure,
    previous_costs,
    current_costs,
    previous_total,
    current_total,
):
    """Notify the family that student-edited choices are awaiting approval."""
    signup, organization = _signup_for_family_notice(signup_id, organization_id)
    if signup is None:
        return False
    recipients = _family_notice_recipients(signup, organization)
    if not recipients:
        return False
    context = {
        "event": signup.event,
        "program": signup.event.program,
        "student": signup.student,
        "previous_departure": previous_departure,
        "current_departure": current_departure,
        "previous_costs": previous_costs,
        "current_costs": current_costs,
        "previous_total": previous_total,
        "current_total": current_total,
        "trip_link": trip_link(signup.event),
    }
    return _delivery_succeeded(
        _send_individually(
            f"Travel: {signup.student} updated trip choices for {signup.event.name}",
            "travel/emails/signup_choices_updated.html",
            context,
            recipients,
            organization,
        )
    )


def _adult_email(adult):
    user = adult.user
    if user and user.email:
        return user.email
    return adult.personal_email or ""


def _adult_context(adult, signup):
    event = signup.event
    return {
        "adult": adult,
        "event": event,
        "program": event.program,
        "departure": signup.departure,
        "trip_link": trip_link(event),
    }


def notify_mentor_signup_created(signup):
    organization = _trusted_notification_organization(signup, signup.adult)
    if organization is None:
        return False
    email = _adult_email(signup.adult)
    if not email:
        return False
    return _delivery_succeeded(
        _send_individually(
            f"Travel: You're going to {signup.event.name}",
            "travel/emails/mentor_signup_confirmed.html",
            _adult_context(signup.adult, signup),
            [email],
            organization,
        )
    )


def notify_mentor_signup_cancelled(signup):
    organization = _trusted_notification_organization(signup, signup.adult)
    if organization is None:
        return False
    email = _adult_email(signup.adult)
    if not email:
        return False
    return _delivery_succeeded(
        _send_individually(
            f"Travel: You're no longer going to {signup.event.name}",
            "travel/emails/mentor_signup_cancelled.html",
            _adult_context(signup.adult, signup),
            [email],
            organization,
        )
    )


def notify_parent_chaperone_signup_created(signup):
    organization = _trusted_notification_organization(signup, signup.adult)
    if organization is None:
        return False
    email = _adult_email(signup.adult)
    if not email:
        return False
    return _delivery_succeeded(
        _send_individually(
            f"Travel: You're signed up to chaperone {signup.event.name}",
            "travel/emails/parent_chaperone_confirmed.html",
            _adult_context(signup.adult, signup),
            [email],
            organization,
        )
    )


def notify_parent_chaperone_signup_cancelled(signup):
    organization = _trusted_notification_organization(signup, signup.adult)
    if organization is None:
        return False
    email = _adult_email(signup.adult)
    if not email:
        return False
    return _delivery_succeeded(
        _send_individually(
            f"Travel: You're no longer chaperoning {signup.event.name}",
            "travel/emails/parent_chaperone_cancelled.html",
            _adult_context(signup.adult, signup),
            [email],
            organization,
        )
    )


def _format_trip_change_value(value, field_name):
    if value in (None, "", False):
        return "Not set"
    if field_name == "amount":
        return f"${value:.2f}"
    return str(value)


def _append_inline_changes(changes, formset, item_name, identity_field):
    for form in formset.forms:
        if not form.has_changed():
            continue

        cleaned_data = form.cleaned_data
        old_identity = form.initial.get(identity_field)
        new_identity = cleaned_data.get(identity_field)
        if cleaned_data.get("DELETE"):
            changes.append(
                {
                    "title": f"{item_name} removed",
                    "detail": _format_trip_change_value(old_identity, identity_field),
                }
            )
            continue

        if form.instance._state.adding:
            details = [
                f"{form.fields[field_name].label}: "
                f"{_format_trip_change_value(cleaned_data.get(field_name), field_name)}"
                for field_name in form.fields
                if field_name in cleaned_data
                and cleaned_data.get(field_name) not in (None, "", False)
                and field_name != "DELETE"
            ]
            changes.append(
                {"title": f"{item_name} added", "detail": "; ".join(details)}
            )
            continue

        for field_name in form.changed_data:
            if field_name == "DELETE":
                continue
            old_value = form.initial.get(field_name)
            new_value = cleaned_data.get(field_name)
            label = form.fields[field_name].label
            changes.append(
                {
                    "title": f"{item_name}: {old_identity or new_identity} — {label}",
                    "detail": "Changed from "
                    f"{_format_trip_change_value(old_value, field_name)} to "
                    f"{_format_trip_change_value(new_value, field_name)}",
                }
            )


def trip_change_summary(event_form, departure_formset, cost_formset):
    """Describe changed event and inline fields without exposing file paths."""
    changes = []
    for field_name in event_form.changed_data:
        old_value = event_form.initial.get(field_name)
        new_value = event_form.cleaned_data.get(field_name)
        if field_name == "permission_form":
            if old_value and new_value:
                detail = "Replaced"
            elif new_value:
                detail = "Added"
            elif old_value:
                detail = "Removed"
            else:
                detail = "Updated"
            changes.append({"title": "Permission form", "detail": detail})
            continue

        changes.append(
            {
                "title": event_form.fields[field_name].label,
                "detail": "Changed from "
                f"{_format_trip_change_value(old_value, field_name)} to "
                f"{_format_trip_change_value(new_value, field_name)}",
            }
        )

    cost_changes_start = len(changes)
    _append_inline_changes(changes, cost_formset, "Cost option", "name")
    for change in changes[cost_changes_start:]:
        if "amount" in change["title"].casefold():
            change["detail"] = (
                "The event cost option amount changed. Families receive their own "
                "signup total separately."
            )
    _append_inline_changes(changes, departure_formset, "Departure", "label")
    return changes


def notify_approved_trip_changes(event_id, organization_id, changes, signup_updates=()):
    """Notify active families and signed-up adults about event edits."""
    if not changes:
        return

    event = (
        TravelEvent.objects.for_organization(organization_id)
        .select_related("organization", "program")
        .filter(pk=event_id)
        .first()
    )
    if event is None:
        return
    organization = event.organization
    if event.program_id and event.program.organization_id != organization.pk:
        return

    signup_updates_by_id = {update["signup_id"]: update for update in signup_updates}
    delivery_results = []
    relationships = (
        AdultStudentRelationship.objects.for_organization(organization)
        .filter(
            organization=organization,
            adult__organization=organization,
            student__organization=organization,
        )
        .select_related("adult", "student")
    )
    signups = (
        TravelSignup.objects.for_organization(organization)
        .filter(
            event=event,
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
        .select_related("student__user", "event", "event__program", "departure")
        .prefetch_related(
            Prefetch(
                "student__adultstudentrelationship_set",
                queryset=relationships,
                to_attr="tenant_contact_relationships",
            )
        )
    )
    family_recipients = set()
    for signup in signups:
        if _trusted_notification_organization(signup, signup.student) != organization:
            continue
        update = signup_updates_by_id.get(signup.pk)
        context = {
            "event": event,
            "program": event.program,
            "student": signup.student,
            "changes": changes,
            "signup_update": update,
            "approval_pending": signup.status == TravelSignup.INTERESTED,
            "permission_form_changed": any(
                change["title"] == "Permission form" for change in changes
            ),
            "trip_link": trip_link(event),
        }
        recipients = _family_notice_recipients(signup, organization)
        if recipients:
            family_recipients.update(email.strip().casefold() for email in recipients)
            delivery_results.append(
                _send_individually(
                    f"Travel: Updates to {event.name}",
                    "travel/emails/event_updated.html",
                    context,
                    recipients,
                    organization,
                )
            )

    adult_recipients = {}
    for model in (TravelMentorSignup, TravelParentChaperoneSignup):
        adult_signups = (
            model.objects.for_organization(organization)
            .filter(
                event=event,
                event__organization=organization,
                adult__organization=organization,
                adult__login_enabled=True,
                adult__email_updates=True,
            )
            .select_related(
                "organization", "event__program", "adult__user", "departure"
            )
        )
        for signup in adult_signups:
            if _trusted_notification_organization(signup, signup.adult) != organization:
                continue
            if signup.departure_id and signup.departure.event_id != event.pk:
                continue
            email = (signup.adult.personal_email or "").strip() or _adult_email(
                signup.adult
            ).strip()
            if email:
                if email.casefold() not in family_recipients:
                    adult_recipients.setdefault(email.casefold(), email)

    context = {
        "event": event,
        "program": event.program,
        "changes": changes,
        "trip_link": trip_link(event),
    }
    if adult_recipients:
        delivery_results.append(
            _send_individually(
                f"Travel: Updates to {event.name}",
                "travel/emails/event_updated.html",
                context,
                adult_recipients.values(),
                organization,
            )
        )
    return bool(delivery_results) and all(
        _delivery_succeeded(result) for result in delivery_results
    )
