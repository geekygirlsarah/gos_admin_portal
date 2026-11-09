import logging
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import CharField, Subquery
from django.db.models.functions import Cast
from django.utils import timezone

from audit.events import AuditEvent
from audit.models import AuditLog
from audit.service import log_event
from organizations.models import Organization, UserOrganizationMembership
from programs.models import Student
from programs.permission_views import user_is_lead_mentor
from programs.utils.notifications import send_templated_notification

logger = logging.getLogger(__name__)


def _distinct_student_view_count(actor, organization, since):
    organization_student_ids = (
        Student.objects.for_organization(organization)
        .annotate(audit_resource_id=Cast("pk", output_field=CharField()))
        .values("audit_resource_id")
    )
    return (
        AuditLog.objects.filter(
            event=AuditEvent.SENSITIVE_DATA_VIEW,
            actor=actor,
            resource_type="Student",
            resource_id__in=Subquery(organization_student_ids),
            timestamp__gte=since,
        )
        .values("resource_id")
        .distinct()
        .count()
    )


def _alert_recipients(organization):
    recipients = {
        address.strip()
        for address in getattr(settings, "ADMIN_EMAILS", [])
        if isinstance(address, str) and address.strip()
    }
    lead_emails = (
        UserOrganizationMembership.objects.filter(
            organization=organization,
            role="LeadMentor",
            is_active=True,
            user__is_active=True,
        )
        .exclude(user__email__isnull=True)
        .exclude(user__email="")
        .values_list("user__email", flat=True)
    )
    recipients.update(email.strip() for email in lead_emails if email.strip())
    return sorted(recipients, key=str.casefold)


def _send_profile_access_alert(recipients, context, organization, actor_id):
    if not recipients:
        return
    try:
        send_templated_notification(
            subject="Potential bulk student profile access detected",
            template_name="programs/emails/profile_access_alert.html",
            context=context,
            recipient_list=recipients,
            organization=organization,
        )
    except Exception as exc:
        logger.warning(
            "Profile access alert email failed "
            "(user_id=%s, organization_id=%s, error=%s)",
            actor_id,
            organization.pk,
            type(exc).__name__,
        )


def check_profile_access_anomaly(
    actor, organization, request=None, reason="profile_views"
):
    """Record and notify on unusually broad student-profile access.

    Historical access is scoped through currently owned Student rows because
    SENSITIVE_DATA_VIEW audit records do not otherwise carry tenant metadata.
    """
    if actor is None or organization is None:
        return False
    if user_is_lead_mentor(actor, org=organization):
        return False

    actor_id = actor.pk
    organization_id = organization.pk
    try:
        threshold = max(1, int(getattr(settings, "PROFILE_ACCESS_ALERT_THRESHOLD", 20)))
        window_seconds = max(
            1, int(getattr(settings, "PROFILE_ACCESS_ALERT_WINDOW_SECONDS", 1800))
        )
        cooldown_seconds = max(
            1, int(getattr(settings, "PROFILE_ACCESS_ALERT_COOLDOWN_SECONDS", 21600))
        )
        now = timezone.now()
        distinct_count = _distinct_student_view_count(
            actor, organization, now - timedelta(seconds=window_seconds)
        )
        if reason != "rate_limit" and distinct_count <= threshold:
            return False

        cooldown_key = f"profile_access_alert:{organization_id}:{actor_id}"
        if not cache.add(cooldown_key, True, timeout=cooldown_seconds):
            return False

        alert = log_event(
            event=AuditEvent.SECURITY_ALERT,
            resource=actor,
            actor=actor,
            request=request,
            after={
                "organization_id": organization_id,
                "distinct_student_count": distinct_count,
                "window_seconds": window_seconds,
                "reason": reason,
                "alerted_at": now.isoformat(),
            },
            notes=(
                f"Potential bulk student profile access: {distinct_count} distinct "
                f"students in {window_seconds} seconds ({reason})."
            ),
        )
        if alert is None:
            cache.delete(cooldown_key)
            return False

        recipients = _alert_recipients(organization)
        organization_for_email: Organization = organization
        context = {
            "actor_email": getattr(actor, "email", ""),
            "actor_name": actor.get_full_name() or actor.get_username(),
            "organization_name": organization.name,
            "alert_timestamp": now,
            "distinct_student_count": distinct_count,
            "window_seconds": window_seconds,
            "reason": reason,
        }
        transaction.on_commit(
            lambda: _send_profile_access_alert(
                recipients, context, organization_for_email, actor_id
            )
        )
        return True
    except Exception as exc:
        logger.warning(
            "Profile access anomaly check failed "
            "(user_id=%s, organization_id=%s, error=%s)",
            actor_id,
            organization_id,
            type(exc).__name__,
        )
        return False
