"""Identity checks for the future application profile-edit workflow."""

from enum import Enum

from applications.services import resolve_application_person_identity
from programs.models import Adult, AdultStudentRelationship, Student


class ApplicationProfileEditAuthorization(str, Enum):
    LINKED_ACCOUNT = "linked_account"
    OWN_PROFILE = "own_profile"
    LINKED_STUDENT = "linked_student"
    BROWSER_UNVERIFIED = "browser_unverified"
    IDENTITY_UNKNOWN = "identity_unknown"
    IDENTITY_AMBIGUOUS = "identity_ambiguous"
    UNRELATED_GUARDIAN = "unrelated_guardian"
    REQUIRES_LEAD_REVIEW = "requires_lead_review"
    APPLICATION_INACTIVE = "application_inactive"
    CROSS_ORGANIZATION = "cross_organization"


def evaluate_application_profile_edit(request, application, target_profile):
    """Return the approved edit authorization without modifying the profile."""
    organization = getattr(request, "organization", None)
    if organization is None:
        return ApplicationProfileEditAuthorization.CROSS_ORGANIZATION
    organization_id = getattr(organization, "pk", None)
    if (
        organization_id != application.organization_id
        or target_profile.organization_id != application.organization_id
    ):
        return ApplicationProfileEditAuthorization.CROSS_ORGANIZATION

    if application.superseded_at is not None or application.status not in (
        application.Status.DRAFT,
        application.Status.EMAIL_VERIFIED,
        application.Status.AWAITING_PARENT,
    ):
        return ApplicationProfileEditAuthorization.APPLICATION_INACTIVE

    user = getattr(request, "user", None)
    if getattr(user, "is_authenticated", False) and target_profile.user_id == getattr(
        user, "pk", None
    ):
        return ApplicationProfileEditAuthorization.LINKED_ACCOUNT

    if target_profile.is_incomplete_application_profile:
        return ApplicationProfileEditAuthorization.REQUIRES_LEAD_REVIEW

    from applications.views.browser import _has_browser_access, _identity

    if not _has_browser_access(request, application):
        return ApplicationProfileEditAuthorization.BROWSER_UNVERIFIED

    identity = _identity(application)
    resolution = resolve_application_person_identity(
        identity["recipient"], application.organization
    )
    if resolution.status == "ambiguous":
        return ApplicationProfileEditAuthorization.IDENTITY_AMBIGUOUS
    if resolution.status != "unique" or resolution.profile is None:
        return ApplicationProfileEditAuthorization.IDENTITY_UNKNOWN
    if resolution.profile.pk == target_profile.pk and type(resolution.profile) is type(
        target_profile
    ):
        return ApplicationProfileEditAuthorization.OWN_PROFILE

    if isinstance(target_profile, Student) and isinstance(resolution.profile, Adult):
        authorized_relationship = (
            AdultStudentRelationship.objects.for_organization(organization)
            .filter(adult_id=resolution.profile.pk, student_id=target_profile.pk)
            .exists()
        )
        if authorized_relationship:
            return ApplicationProfileEditAuthorization.LINKED_STUDENT
        return ApplicationProfileEditAuthorization.UNRELATED_GUARDIAN

    return ApplicationProfileEditAuthorization.REQUIRES_LEAD_REVIEW
