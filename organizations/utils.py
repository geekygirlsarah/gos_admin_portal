from typing import Any, Dict, List, Optional

from django.conf import settings

from .models import (
    DEFAULT_ORGANIZATION_SETTINGS,
    Organization,
    OrganizationSettings,
    SettingsDict,
)


def get_site_base_url() -> str:
    """Return the base URL (including scheme and host) for the site."""
    site_url = (
        getattr(settings, "SITE_URL", None)
        or getattr(settings, "BASE_URL", None)
        or getattr(settings, "CANONICAL_DOMAIN", None)
        or ""
    )
    if site_url:
        return str(site_url).rstrip("/")

    import os

    env_url = (
        os.getenv("SITE_URL")
        or os.getenv("BASE_URL")
        or os.getenv("CANONICAL_DOMAIN")
        or os.getenv("RENDER_EXTERNAL_URL")
    )
    if env_url:
        return env_url.rstrip("/")

    render_host = os.getenv("RENDER_EXTERNAL_HOSTNAME")
    if render_host:
        return f"https://{render_host}"

    allowed_hosts = getattr(settings, "ALLOWED_HOSTS", [])
    valid_hosts = [
        h
        for h in allowed_hosts
        if h
        and not h.startswith(".")
        and h not in ("*", "localhost", "127.0.0.1", "0.0.0.0")  # nosec B104
    ]
    if valid_hosts:
        return f"https://{valid_hosts[0]}"

    return ""


def _resolve_portal_url(raw_cfg: Dict[str, Any], org: Optional[Organization]) -> str:
    """Resolve the public portal URL from email config, custom domain, or site settings."""
    portal_url = raw_cfg.get("portal_url") or ""
    if not portal_url:
        custom_domain_cfg = raw_cfg.get("custom_domain") or {}
        if custom_domain_cfg.get("enabled") and custom_domain_cfg.get("domain"):
            domain_val = str(custom_domain_cfg.get("domain")).strip()
            if domain_val:
                portal_url = (
                    domain_val
                    if domain_val.startswith(("http://", "https://"))
                    else f"https://{domain_val}"
                )
    if (
        not portal_url
        and org
        and getattr(org, "settings", None)
        and isinstance(org.settings, dict)
    ):
        portal_url = (
            org.settings.get("portal_url") or org.settings.get("site_url") or ""
        )
    if not portal_url:
        portal_url = get_site_base_url()

    if portal_url:
        portal_url = str(portal_url).strip()
        if not portal_url.startswith(("http://", "https://")):
            portal_url = f"https://{portal_url}"
        portal_url = portal_url.rstrip("/")

    return portal_url


def get_default_organization() -> Optional[Organization]:
    """Return the active or single seeded organization (or None)."""
    try:
        from GoSAdminPortal.middleware import _default_organization

        return _default_organization()
    except Exception:
        try:
            return Organization.objects.filter(is_active=True).first()
        except Exception:
            return None


def get_organization_settings(
    organization: Optional[Organization] = None,
) -> SettingsDict:
    """Return the resolved SettingsDict for an organization (or default organization)."""
    org = organization or get_default_organization()
    if org is not None:
        if isinstance(org.settings, SettingsDict):
            return org.settings
        return SettingsDict(org.settings)
    return SettingsDict({})


def get_organization_setting(
    organization: Optional[Organization] = None,
    key: str = "",
    default: Any = None,
) -> Any:
    """Retrieve a single setting for an organization with platform defaults."""
    settings = get_organization_settings(organization)
    return settings.get(key, default)


def get_organization_email_settings(
    organization: Optional[Organization] = None,
) -> Dict[str, Any]:
    """Retrieve structured email settings for an organization with platform fallbacks."""
    org = None
    try:
        org = organization or get_default_organization()
    except Exception:
        org = None

    org_settings = None
    if org:
        try:
            org_settings = OrganizationSettings.objects.filter(organization=org).first()
        except Exception:
            org_settings = None

    raw_cfg = (
        org_settings.data.get("email_settings", {})
        if org_settings and org_settings.data
        else {}
    )

    default_name = getattr(settings, "DEFAULT_FROM_NAME", None)
    default_reply_to = getattr(
        settings, "DEFAULT_REPLY_TO_EMAIL", "leads@girlsofsteelrobotics.org"
    )
    default_from_email = getattr(settings, "NOTIFICATION_FROM_EMAIL", None) or getattr(
        settings, "DEFAULT_FROM_EMAIL", "notifications@girlsofsteelrobotics.org"
    )

    sender_display_name = (
        raw_cfg.get("sender_display_name")
        or (org.name if (org and org.slug != "gos") else None)
        or default_name
        or (org.name if org else "Girls of Steel Notifications")
        or "Girls of Steel Notifications"
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

    # Email Branding & Template Customization Settings
    primary_color = raw_cfg.get("primary_color") or "#4a154b"
    secondary_color = raw_cfg.get("secondary_color") or "#f8f9fa"
    header_text_color = raw_cfg.get("header_text_color") or "#ffffff"
    logo_url = raw_cfg.get("logo_url") or ""
    if logo_url and logo_url.startswith("/"):
        base_url = get_site_base_url()
        if base_url:
            logo_url = f"{base_url}{logo_url}"
    organization_name = (
        raw_cfg.get("organization_name")
        or (org.name if org else default_name)
        or default_name
    )
    address_line1 = (
        raw_cfg.get("address_line1")
        if "address_line1" in raw_cfg
        else "Carnegie Mellon University"
    )
    address_line2 = (
        raw_cfg.get("address_line2")
        if "address_line2" in raw_cfg
        else "5000 Forbes Ave, Pittsburgh, PA 15213-3890"
    )
    footer_text = (
        raw_cfg.get("footer_text")
        if "footer_text" in raw_cfg
        else "Thank you for being part of Girls of Steel Robotics!"
    )
    portal_url = _resolve_portal_url(raw_cfg, org)

    contact_name = (
        raw_cfg.get("contact_name")
        if "contact_name" in raw_cfg
        else "Elizabeth Kysel, Sarah Withee"
    )
    contact_email = (
        raw_cfg.get("contact_email")
        or reply_to_email
        or "leads@girlsofsteelrobotics.org"
    )

    return {
        "sender_display_name": sender_display_name,
        "reply_to_email": reply_to_email,
        "notification_from_email": notification_from_email,
        "sender_accounts": sender_accounts,
        "custom_domain": custom_domain,
        "primary_color": primary_color,
        "secondary_color": secondary_color,
        "header_text_color": header_text_color,
        "logo_url": logo_url,
        "organization_name": organization_name,
        "address_line1": address_line1,
        "address_line2": address_line2,
        "footer_text": footer_text,
        "portal_url": portal_url,
        "contact_name": contact_name,
        "contact_email": contact_email,
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
