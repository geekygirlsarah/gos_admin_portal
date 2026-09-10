"""Status emails for the travel feature.

Every travel signup state transition emails the people it affects -- the
student (when they have a linked account) and/or their parents -- using
``send_templated_notification`` exactly like the fee/payment notifications in
``programs/signals.py``.
"""

from django.urls import reverse

from programs.utils import send_templated_notification
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
        )

    parent_to = parent_recipients(signup.student)
    if parent_to:
        send_templated_notification(
            f"Travel: {signup.student} wants to go to {signup.event.name} - please approve",
            "travel/emails/parent_approval_requested.html",
            context,
            parent_to,
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
        )
    student_to = student_recipients(signup.student)
    if student_to:
        send_templated_notification(
            f"Travel: You are no longer going to {signup.event.name}",
            "travel/emails/signup_withdrawn.html",
            context,
            student_to,
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
    )
