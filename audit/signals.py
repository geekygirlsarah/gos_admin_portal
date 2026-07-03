import logging

from django.contrib.auth import get_user_model
from django.contrib.auth.signals import (
    user_logged_in,
    user_logged_out,
    user_login_failed,
)
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from .events import AuditEvent
from .models import AuditLog
from .service import log_event

# We use standard Django signals for login/logout to ensure we catch
# both allauth and standard Django auth events.


User = get_user_model()
logger = logging.getLogger(__name__)


@receiver(pre_save, sender=User)
def track_user_changes(sender, instance, **kwargs):
    if not instance.pk:
        return

    try:
        old_instance = User.objects.get(pk=instance.pk)
    except User.DoesNotExist:
        return

    # Track deactivation
    if old_instance.is_active and not instance.is_active:
        log_event(
            event=AuditEvent.ACCOUNT_DEACTIVATED,
            resource=instance,
            before={"is_active": True},
            after={"is_active": False},
        )
    elif not old_instance.is_active and instance.is_active:
        # Optional: track reactivation? The issue didn't explicitly ask but it makes sense.
        pass

    # Track role changes (staff/superuser)
    roles_to_check = ["is_staff", "is_superuser"]
    before_roles = {}
    after_roles = {}
    role_changed = False

    for role in roles_to_check:
        old_val = getattr(old_instance, role)
        new_val = getattr(instance, role)
        if old_val != new_val:
            before_roles[role] = old_val
            after_roles[role] = new_val
            role_changed = True

    if role_changed:
        log_event(
            event=AuditEvent.ROLE_CHANGED,
            resource=instance,
            before=before_roles,
            after=after_roles,
        )

    # Track password resets
    if old_instance.password != instance.password:
        # We don't log the password itself, just that it changed.
        log_event(
            event=AuditEvent.PASSWORD_RESET,
            resource=instance,
            notes="Password hash changed.",
        )


@receiver(user_logged_in)
def log_user_login(sender, request, user, **kwargs):
    log_event(
        event=AuditEvent.USER_LOGIN,
        resource=user,
        actor=user,
        request=request,
        outcome=AuditLog.SUCCESS,
        notes=f"User {user.email} logged in successfully.",
    )


@receiver(user_logged_out)
def log_user_logout(sender, request, user, **kwargs):
    if user:
        log_event(
            event=AuditEvent.USER_LOGOUT,
            resource=user,
            request=request,
            outcome=AuditLog.SUCCESS,
            notes=f"User {user.email} logged out.",
        )


@receiver(user_login_failed)
def log_user_login_failed(sender, credentials, request, **kwargs):
    email = credentials.get("email") or credentials.get("username") or "unknown"
    # Try to find the user if they exist to link the resource, but it's not strictly necessary.
    # If user doesn't exist, resource=None might be tricky for log_event.
    # log_event requires a resource. I'll use the User model class or a placeholder if needed.
    # Actually, AuditLog.resource_id is a string.

    user = (
        User.objects.filter(email=email).first()
        or User.objects.filter(username=email).first()
    )

    if user:
        log_event(
            event=AuditEvent.LOGIN_FAILED,
            resource=user,
            request=request,
            outcome=AuditLog.FAILURE,
            notes=f"Failed login attempt for existing user: {email}",
        )
    else:
        # For non-existent users, we still want to log it.
        # I'll log against the User model class if log_event supports it,
        # or just create a log entry manually if not.
        # log_event(..., resource=User, ...) might not work if it expects an instance.

        # Let's check audit/service.py again.
        # It does resource_type=type(resource).__name__, resource_id=str(resource.pk)

        # If I don't have a user, I can't easily use log_event without a dummy.
        # I'll just use a dummy user or just log it without an actor.

        # Actually, let's just log it as a system event if no user.
        # But log_event needs a resource.

        # I'll try to find a system-wide resource or just skip linking to a specific user.
        # Maybe use the anonymous user?

        from django.contrib.auth.models import AnonymousUser

        dummy_resource = AnonymousUser()
        # AnonymousUser has no pk. log_event will fail on resource.pk.

        # I'll just log it manually to AuditLog if log_event is too restrictive.
        AuditLog.objects.create(
            event=AuditEvent.LOGIN_FAILED,
            resource_type="User",
            resource_id="0",
            resource_repr=f"Non-existent user: {email}",
            ip_address=request.META.get("REMOTE_ADDR") if request else None,
            outcome=AuditLog.FAILURE,
            notes=f"Failed login attempt for non-existent user: {email}",
        )
