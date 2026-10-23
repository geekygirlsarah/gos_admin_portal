import json
from typing import Any, Dict, List, Optional

from django.conf import settings

from .models import Organization, OrganizationSettings


def get_default_organization() -> Optional[Organization]:
    """Return the active or single seeded organization (or None)."""
    try:
        from GoSAdminPortal.middleware import _default_organization

        return _default_organization()
    except Exception:
        return Organization.objects.filter(is_active=True).first()


def get_organization_email_settings(
    organization: Optional[Organization] = None,
) -> Dict[str, Any]:
    """Retrieve structured email settings for an organization with platform fallbacks."""
    org = organization or get_default_organization()
    org_settings = None
    if org:
        org_settings = OrganizationSettings.objects.filter(organization=org).first()

    raw_cfg = (
        org_settings.data.get("email_settings", {})
        if org_settings and org_settings.data
        else {}
    )

    default_name = getattr(
        settings, "DEFAULT_FROM_NAME", "Girls of Steel Notifications"
    )
    default_reply_to = getattr(
        settings, "DEFAULT_REPLY_TO_EMAIL", "leads@girlsofsteelrobotics.org"
    )
    default_from_email = getattr(settings, "NOTIFICATION_FROM_EMAIL", None) or getattr(
        settings, "DEFAULT_FROM_EMAIL", "notifications@girlsofsteelrobotics.org"
    )

    sender_display_name = (
        raw_cfg.get("sender_display_name")
        or (org.name if org else default_name)
        or default_name
    )
    reply_to_email = raw_cfg.get("reply_to_email") or default_reply_to
    notification_from_email = (
        raw_cfg.get("notification_from_email") or default_from_email
    )
    sender_accounts = raw_cfg.get("sender_accounts", [])
    custom_domain = raw_cfg.get(
        "custom_domain",
        {
            "enabled": False,
            "domain": "",
            "from_email": "",
        },
    )

    return {
        "sender_display_name": sender_display_name,
        "reply_to_email": reply_to_email,
        "notification_from_email": notification_from_email,
        "sender_accounts": sender_accounts,
        "custom_domain": custom_domain,
    }


def get_sender_accounts(
    organization: Optional[Organization] = None,
) -> List[Dict[str, Any]]:
    """Return configured sender identities for the organization or global fallback."""
    cfg = get_organization_email_settings(organization)
    accounts = cfg.get("sender_accounts", [])
    if not accounts:
        # Fall back to settings.EMAIL_SENDER_ACCOUNTS if no org accounts configured
        accounts = getattr(settings, "EMAIL_SENDER_ACCOUNTS", []) or []
    return accounts


def update_organization_email_settings(
    organization: Organization,
    email_settings: Dict[str, Any],
) -> OrganizationSettings:
    """Save email settings into OrganizationSettings.data."""
    org_settings, _ = OrganizationSettings.objects.get_or_create(
        organization=organization
    )
    if not isinstance(org_settings.data, dict):
        org_settings.data = {}
    org_settings.data["email_settings"] = email_settings
    org_settings.save(update_fields=["data", "updated_at"])
    return org_settings
