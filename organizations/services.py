"""Services for organization member invitations and onboarding workflows."""

import logging

from django.urls import reverse

from organizations.models import OrganizationInvitation
from organizations.utils import (
    get_organization_email_settings,
    get_organization_url,
)
from programs.utils.notifications import send_templated_notification

logger = logging.getLogger(__name__)


def send_invitation_email(invitation: OrganizationInvitation, request=None) -> bool:
    """Send an invitation email to the recipient with an acceptance link and expiration notice."""
    try:
        org = invitation.organization
        org_cfg = get_organization_email_settings(org)

        # Resolve base URL and accept link
        base_url = get_organization_url(org)
        if not base_url and request:
            base_url = request.build_absolute_uri("/")[:-1]

        path = reverse(
            "organizations:accept_invitation", kwargs={"token": invitation.token}
        )
        invitation_url = f"{base_url.rstrip('/')}{path}" if base_url else path

        inviter_name = ""
        if invitation.invited_by:
            inviter = invitation.invited_by
            inviter_name = (
                (
                    getattr(inviter, "adult_profile", None)
                    and inviter.adult_profile.display_name
                )
                or (
                    getattr(inviter, "student_profile", None)
                    and inviter.student_profile.display_name
                )
                or inviter.get_full_name()
                or inviter.email
                or inviter.username
            )

        context = {
            "invitation": invitation,
            "organization": org,
            "invitation_url": invitation_url,
            "inviter_name": inviter_name,
            "role_display": invitation.get_role_display(),
            "expires_at": invitation.expires_at,
            "org_email_settings": org_cfg,
        }

        subject = f"Invitation to join {org.name} as {invitation.get_role_display()}"

        send_templated_notification(
            subject=subject,
            template_name="organizations/emails/invitation.html",
            context=context,
            recipient_list=[invitation.email],
            organization=org,
        )
        return True
    except Exception as e:
        logger.error(
            "Failed to send invitation email to %s: %s",
            invitation.email,
            e,
            exc_info=True,
        )
        return False
