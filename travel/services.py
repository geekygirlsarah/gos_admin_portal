"""Status emails for the travel feature.

Every travel signup state transition emails the people it affects -- the
student (when they have a linked account) and/or their parents -- using
``send_templated_notification`` exactly like the fee/payment notifications in
``programs/signals.py``.
"""

from django.urls import reverse

from programs.utils import send_templated_notification
from travel.models import (
    TravelEvent,
    TravelMentorSignup,
    TravelParentChaperoneSignup,
    TravelSignup,
)
from travel.utils import parent_recipients, student_recipients


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
    context = _base_context(signup)

    student_to = student_recipients(signup.student)
    if student_to:
        send_templated_notification(
            f"Travel: You signed up for {signup.event.name}",
            "travel/emails/signup_created.html",
            context,
            student_to,
            organization=_trusted_notification_organization(signup, signup.student),
        )

    parent_to = parent_recipients(signup.student)
    if parent_to:
        send_templated_notification(
            f"Travel: {signup.student} wants to go to {signup.event.name} - please approve",
            "travel/emails/parent_approval_requested.html",
            context,
            parent_to,
            organization=_trusted_notification_organization(signup, signup.student),
        )


def notify_signup_approved(signup):
    recipient = student_recipients(signup.student) or parent_recipients(signup.student)
    if not recipient:
        return
    context = _base_context(signup)
    send_templated_notification(
        f"Travel: {signup.event.name} signup approved",
        "travel/emails/signup_approved.html",
        context,
        recipient,
        organization=_trusted_notification_organization(signup, signup.student),
    )


def notify_signup_declined(signup):
    recipient = student_recipients(signup.student) or parent_recipients(signup.student)
    if not recipient:
        return
    context = _base_context(signup)
    send_templated_notification(
        f"Travel: {signup.event.name} signup declined",
        "travel/emails/signup_declined.html",
        context,
        recipient,
        organization=_trusted_notification_organization(signup, signup.student),
    )


def notify_signup_withdrawn(signup):
    """Student (or a lead mentor for them) backed out: let the parents know and
    (when the student has an account) the student."""
    context = _base_context(signup)
    parent_to = parent_recipients(signup.student)
    if parent_to:
        send_templated_notification(
            f"Travel: {signup.student} is no longer going to {signup.event.name}",
            "travel/emails/signup_withdrawn.html",
            context,
            parent_to,
            organization=_trusted_notification_organization(signup, signup.student),
        )
    student_to = student_recipients(signup.student)
    if student_to:
        send_templated_notification(
            f"Travel: You are no longer going to {signup.event.name}",
            "travel/emails/signup_withdrawn.html",
            context,
            student_to,
            organization=_trusted_notification_organization(signup, signup.student),
        )


def notify_signup_decision_undone(signup):
    """A parent rescinded an earlier approve/decline; it's back to 'interested'."""
    recipient = student_recipients(signup.student) or parent_recipients(signup.student)
    if not recipient:
        return
    context = _base_context(signup)
    send_templated_notification(
        f"Travel: {signup.event.name} decision updated",
        "travel/emails/signup_decision_undone.html",
        context,
        recipient,
        organization=_trusted_notification_organization(signup, signup.student),
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
    email = _adult_email(signup.adult)
    if not email:
        return
    send_templated_notification(
        f"Travel: You're going to {signup.event.name}",
        "travel/emails/mentor_signup_confirmed.html",
        _adult_context(signup.adult, signup),
        [email],
        organization=_trusted_notification_organization(signup, signup.adult),
    )


def notify_mentor_signup_cancelled(signup):
    email = _adult_email(signup.adult)
    if not email:
        return
    send_templated_notification(
        f"Travel: You're no longer going to {signup.event.name}",
        "travel/emails/mentor_signup_cancelled.html",
        _adult_context(signup.adult, signup),
        [email],
        organization=_trusted_notification_organization(signup, signup.adult),
    )


def notify_parent_chaperone_signup_created(signup):
    email = _adult_email(signup.adult)
    if not email:
        return
    send_templated_notification(
        f"Travel: You're signed up to chaperone {signup.event.name}",
        "travel/emails/parent_chaperone_confirmed.html",
        _adult_context(signup.adult, signup),
        [email],
        organization=_trusted_notification_organization(signup, signup.adult),
    )


def notify_parent_chaperone_signup_cancelled(signup):
    email = _adult_email(signup.adult)
    if not email:
        return
    send_templated_notification(
        f"Travel: You're no longer chaperoning {signup.event.name}",
        "travel/emails/parent_chaperone_cancelled.html",
        _adult_context(signup.adult, signup),
        [email],
        organization=_trusted_notification_organization(signup, signup.adult),
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

    _append_inline_changes(changes, departure_formset, "Departure", "label")
    _append_inline_changes(changes, cost_formset, "Cost option", "name")
    return changes


def notify_approved_trip_changes(event_id, organization_id, changes):
    """Notify approved students, their guardians, and signed-up adult attendees."""
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

    signups = (
        TravelSignup.objects.for_organization(organization)
        .filter(
            event=event,
            event__organization=organization,
            student__organization=organization,
            status=TravelSignup.APPROVED,
        )
        .select_related("student", "event", "departure")
    )
    recipients = {}
    for signup in signups:
        if _trusted_notification_organization(signup, signup.student) != organization:
            continue
        for email in student_recipients(signup.student) + parent_recipients(
            signup.student, organization=organization
        ):
            normalized_email = email.strip().casefold()
            if normalized_email:
                recipients.setdefault(normalized_email, email.strip())

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
                recipients.setdefault(email.casefold(), email)

    context = {
        "event": event,
        "program": event.program,
        "changes": changes,
        "trip_link": trip_link(event),
    }
    for email in recipients.values():
        send_templated_notification(
            f"Travel: Updates to {event.name}",
            "travel/emails/event_updated.html",
            context,
            [email],
            organization=organization,
        )
