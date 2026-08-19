from django.db.models import QuerySet

from ..models import Adult


def active_adults() -> QuerySet:
    """Return a queryset of all active adults (login enabled)."""
    return Adult.objects.filter(login_enabled=True)


def active_mentors() -> QuerySet:
    """Return a queryset of active mentors."""
    return Adult.objects.filter(is_mentor=True, mentor_active=True)


def active_parents() -> QuerySet:
    """Return a queryset of active parents."""
    return active_adults().filter(is_parent=True)


def active_alumni() -> QuerySet:
    """Return a queryset of active alumni."""
    return active_adults().filter(is_alumni=True)
