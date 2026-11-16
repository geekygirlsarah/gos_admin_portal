from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from organizations.models import UserOrganizationMembership
from programs.models import Notification, UserNotificationStatus

MEMBERSHIP_NOTIFICATION_ROLES = {
    "LeadMentor": "lead_mentor",
    "Mentor": "mentor",
    "Parent": "parent",
    "Student": "student",
    "Alumni": "alumni",
}


def notification_roles_for_user(user, organization):
    if organization is None:
        return {"admin"} if user.is_active and user.is_superuser else set()

    membership_roles = list(
        UserOrganizationMembership.objects.filter(
            user=user,
            organization=organization,
            is_active=True,
        ).values_list("role", flat=True)
    )
    if not membership_roles:
        return None
    return {
        MEMBERSHIP_NOTIFICATION_ROLES[role]
        for role in membership_roles
        if role in MEMBERSHIP_NOTIFICATION_ROLES
    }


def visible_notifications_for_user(user, organization):
    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return []

    roles = notification_roles_for_user(user, organization)
    if roles is None or (organization is None and not roles):
        return []
    roles = roles or set()

    if organization is None:
        queryset = Notification.objects.filter(
            organization__isnull=True,
            is_platform_wide=True,
        )
    else:
        queryset = Notification.objects.for_organization(organization) | (
            Notification.objects.filter(
                organization__isnull=True,
                is_platform_wide=True,
            )
        )

    read_receipt = UserNotificationStatus.objects.filter(
        user=user,
        notification_id=OuterRef("pk"),
        is_seen=True,
    )
    queryset = (
        queryset.filter(is_active=True)
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()))
        .annotate(is_seen=Exists(read_receipt))
        .select_related("created_by", "organization")
        .order_by("-created_at")
    )
    return [
        notification
        for notification in queryset
        if not notification.target_roles
        or roles.intersection(notification.target_roles)
    ]


def unread_notification_count(user, organization):
    return sum(
        not notification.is_seen
        for notification in visible_notifications_for_user(user, organization)
    )
