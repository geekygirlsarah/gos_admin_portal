from typing import Any, Dict, List, Optional

from django.conf import settings
from django.db import models

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


def get_organization_url(organization: Optional[Organization] = None) -> str:
    """Return the canonical public URL for an organization."""
    if not organization:
        return get_site_base_url()

    org_settings = getattr(organization, "settings", {}) or {}
    if isinstance(org_settings, dict):
        custom_domain = (
            org_settings.get("custom_domain") or org_settings.get("domain") or ""
        )
        if custom_domain:
            custom_domain = str(custom_domain).strip()
            return (
                custom_domain
                if custom_domain.startswith(("http://", "https://"))
                else f"https://{custom_domain}"
            )

    subdomain = getattr(organization, "subdomain", None) or getattr(
        organization, "slug", None
    )
    root_domain = getattr(settings, "MOMENTUM_ROOT_DOMAIN", "momentumteam.app")
    if subdomain and root_domain:
        return f"https://{subdomain}.{root_domain}"

    return get_site_base_url()


def resolve_organization_from_request(request) -> Optional[Organization]:
    """Resolve the active Organization from request headers, host/subdomain, session, or default fallback."""
    if request is None:
        return get_default_organization()

    cached = getattr(request, "_cached_organization", None)
    if cached is not None:
        return cached

    meta = getattr(request, "META", {}) or {}

    # 1. Check HTTP headers (e.g. X-Organization, X-Org-Slug, X-Org-Subdomain)
    header_val = (
        meta.get("HTTP_X_ORGANIZATION")
        or meta.get("HTTP_X_ORG_SLUG")
        or meta.get("HTTP_X_ORG_SUBDOMAIN")
        or ""
    )
    if header_val:
        header_val = str(header_val).strip()
        org = Organization.objects.filter(
            models.Q(slug__iexact=header_val) | models.Q(subdomain__iexact=header_val),
            is_active=True,
        ).first()
        if org:
            request._cached_organization = org
            return org

    # 2. Check Host / Subdomain
    host = meta.get("HTTP_HOST") or meta.get("SERVER_NAME") or ""
    if not host:
        try:
            host = request.get_host()
        except Exception:
            host = ""
    host = host.split(":")[0].strip().lower()

    root_domain = getattr(settings, "MOMENTUM_ROOT_DOMAIN", "momentumteam.app").lower()
    root_domains = {
        root_domain,
        f"www.{root_domain}",
        "momentumteam.app",
        "www.momentumteam.app",
    }

    # The root platform domain (momentumteam.app / www) serves the marketing site
    # and has no organization context.
    if host in root_domains:
        request._cached_organization = None
        return None

    # Girls of Steel legacy domains / CNAME targets
    gos_domains = {
        "portal.girlsofsteelrobotics.org",
        "girlsofsteelrobotics.org",
        "gosadmin.com",
    }
    if host in gos_domains:
        org = Organization.objects.filter(slug="gos", is_active=True).first()
        if org:
            request._cached_organization = org
            return org

    if host and host not in (
        "testserver",
        "localhost",
        "127.0.0.1",
    ):
        # Check active organization custom domain settings
        for active_org in Organization.objects.filter(is_active=True):
            org_settings = getattr(active_org, "settings", {}) or {}
            custom_domain_cfg = org_settings.get("custom_domain") or {}
            custom_domain_val = ""
            if isinstance(custom_domain_cfg, dict):
                custom_domain_val = custom_domain_cfg.get("domain") or ""
            elif isinstance(custom_domain_cfg, str):
                custom_domain_val = custom_domain_cfg
            if not custom_domain_val:
                custom_domain_val = org_settings.get("domain") or ""
            if custom_domain_val:
                clean_custom = (
                    custom_domain_val.replace("https://", "")
                    .replace("http://", "")
                    .split("/")[0]
                    .split(":")[0]
                    .strip()
                    .lower()
                )
                if clean_custom and clean_custom == host:
                    request._cached_organization = active_org
                    return active_org

        parts = host.split(".")
        if len(parts) >= 2:
            candidate = parts[0]
            reserved = {
                "www",
                "api",
                "app",
                "admin",
                "mail",
                "portal",
                "staging",
                "dev",
                "127",
                "testserver",
                "localhost",
            }
            if candidate not in reserved:
                org = Organization.objects.filter(
                    models.Q(subdomain__iexact=candidate)
                    | models.Q(slug__iexact=candidate),
                    is_active=True,
                ).first()
                if org:
                    request._cached_organization = org
                    return org
        else:
            org = Organization.objects.filter(
                models.Q(subdomain__iexact=host) | models.Q(slug__iexact=host),
                is_active=True,
            ).first()
            if org:
                request._cached_organization = org
                return org

    # 3. Check session active organization
    session = getattr(request, "session", None)
    if session:
        session_org_id = session.get("active_organization_id")
        if session_org_id:
            org = Organization.objects.filter(pk=session_org_id, is_active=True).first()
            if org:
                request._cached_organization = org
                return org

    org = get_default_organization()
    request._cached_organization = org
    return org


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
