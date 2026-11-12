from django.conf import settings
from django.db import models

from organizations.models import OrganizationManager


class Notification(models.Model):
    """An in-portal update scoped to an organization or the whole platform."""

    TYPE_CHOICES = [
        ("info", "Information"),
        ("warning", "Important"),
        ("error", "Urgent"),
        ("success", "Success"),
    ]

    ROLE_CHOICES = [
        ("admin", "Platform administrators"),
        ("student", "Students"),
        ("parent", "Parents"),
        ("mentor", "Mentors"),
        ("lead_mentor", "Lead mentors"),
        ("alumni", "Alumni"),
    ]

    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notifications",
    )
    is_platform_wide = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_notifications",
    )
    title = models.CharField(max_length=255)
    message = models.TextField()
    notification_type = models.CharField(
        max_length=20, choices=TYPE_CHOICES, default="info"
    )
    target_roles = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    objects = OrganizationManager()

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(is_platform_wide=False)
                | models.Q(organization__isnull=True),
                name="notification_platform_scope_global_only",
            )
        ]
        indexes = [
            models.Index(
                fields=["organization", "is_active", "expires_at"],
                name="prog_notif_scope_active_idx",
            )
        ]

    def __str__(self):
        return f"{self.title} ({self.notification_type})"


class UserNotificationStatus(models.Model):
    """A per-user read receipt for an in-portal update."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notification_statuses",
    )
    notification = models.ForeignKey(
        Notification,
        on_delete=models.CASCADE,
        related_name="read_receipts",
    )
    is_seen = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "notification"],
                name="unique_user_notification_status",
            )
        ]
        indexes = [models.Index(fields=["user", "is_seen"])]

    def __str__(self):
        state = "Seen" if self.is_seen else "Unseen"
        return f"{self.user} - {self.notification.title} ({state})"
