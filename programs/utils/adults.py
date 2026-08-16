from django.db.models import QuerySet

from ..models import Adult


def active_adults() -> QuerySet:
    """Return a queryset of all active adults."""
    return Adult.objects.filter(active=True)


def active_mentors() -> QuerySet:
    """Return a queryset of active mentors."""
    return active_adults().filter(is_mentor=True)


def active_parents() -> QuerySet:
    """Return a queryset of active parents."""
    return active_adults().filter(is_parent=True)


def active_alumni() -> QuerySet:
    """Return a queryset of active alumni."""
    return active_adults().filter(is_alumni=True)
