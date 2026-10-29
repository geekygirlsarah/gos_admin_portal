from django.db.models import QuerySet

from ..models import Adult


def active_adults(organization=None) -> QuerySet:
    """Return a queryset of all active adults (login enabled)."""
    return Adult.objects.for_organization(organization).filter(login_enabled=True)


def active_mentors(organization=None) -> QuerySet:
    """Return a queryset of active mentors."""
    return Adult.objects.for_organization(organization).filter(
        is_mentor=True, mentor_active=True
    )


def active_parents(organization=None) -> QuerySet:
    """Return a queryset of active parents."""
    return active_adults(organization).filter(is_parent=True)


def active_alumni(organization=None) -> QuerySet:
    """Return a queryset of active alumni."""
    return active_adults(organization).filter(is_alumni=True)
