"""Helpers for the travel feature.

Recipient filtering mirrors the fee/payment notification pattern in
``programs/signals.py``: only parents with portal login enabled, email updates
opted-in and a personal email on file are notified.
"""

from django.db.models import F, Prefetch


def signed_permission_form_prefetches(organization):
    """Prefetch current and superseded local signed forms for signup displays."""
    from travel.models import TravelSignedPermissionForm

    forms = (
        TravelSignedPermissionForm.objects.for_organization(organization)
        .filter(
            organization=organization,
            signup__organization=organization,
            signup__event__organization=organization,
            signup__student__organization=organization,
        )
        .exclude(file="")
    )
    return (
        Prefetch(
            "signed_permission_forms",
            queryset=forms.filter(
                form_version=F("signup__event__permission_form_version")
            ).order_by("-uploaded_at", "-pk"),
            to_attr="current_signed_permission_forms",
        ),
        Prefetch(
            "signed_permission_forms",
            queryset=forms.exclude(
                form_version=F("signup__event__permission_form_version")
            ).order_by("-uploaded_at", "-pk"),
            to_attr="outdated_signed_permission_forms",
        ),
    )


def parent_recipients(student, organization=None):
    """Unique parent emails we are allowed to notify for ``student``."""
    emails = []
    seen = set()
    for parent in student.all_parents:
        if organization is not None and parent.organization_id != organization.pk:
            continue
        if not (
            parent.login_enabled and parent.email_updates and parent.personal_email
        ):
            continue
        if parent.personal_email in seen:
            continue
        seen.add(parent.personal_email)
        emails.append(parent.personal_email)
    return emails


def student_recipients(student):
    """The student's own linked portal email, if they have one."""
    user = student.user
    if user and user.email:
        return [user.email]
    return []


def get_student_travel_highlights(student, program):
    """Upcoming trips this student is signed up for, for the dashboard card."""
    from datetime import date

    from django.db.models import F, Q

    from travel.models import TravelSignup

    organization = student.organization
    signups = (
        TravelSignup.objects.for_organization(organization)
        .filter(
            organization=organization,
            student=student,
            student__organization=organization,
            event__organization=organization,
            event__program=program,
            event__program__organization=organization,
            event__end_date__gte=date.today(),
        )
        .filter(
            Q(departure__isnull=True)
            | Q(
                departure__organization=organization,
                departure__event_id=F("event_id"),
            )
        )
        .select_related("student", "event", "departure", "approved_by")
        .prefetch_related(
            "cost_items", *signed_permission_form_prefetches(organization)
        )
    )
    return [
        s
        for s in signups
        if s.status in (TravelSignup.INTERESTED, TravelSignup.APPROVED)
    ]


def get_parent_travel_highlights(student, program):
    """Signups needing (or that received) this parent's attention, for the
    dashboard card."""
    from travel.models import TravelSignup

    signups = get_student_travel_highlights(student, program)
    pending = [s for s in signups if s.status == TravelSignup.INTERESTED]
    return signups, pending


def get_mentor_travel_signups(adult):
    """Upcoming trips an adult is going on as a mentor."""
    from travel.models import TravelMentorSignup

    signups = (
        TravelMentorSignup.objects.filter(adult=adult)
        .select_related("event", "event__program", "departure")
        .order_by("event__start_date", "event__name")
    )
    return [s for s in signups if not s.event.is_past]
