from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import models
from django.utils.functional import lazy

from .domain_utils import (
    LEGACY_ORGANIZATION_HOSTNAMES,
    get_legacy_custom_hostname,
    is_momentum_hostname,
    is_valid_domain_label,
)
from .models import (
    DEFAULT_ORGANIZATION_SETTINGS,
    Organization,
    OrganizationDomain,
    OrganizationSettings,
    SettingsDict,
)

_PAYMENT_URL_VALIDATOR = URLValidator(schemes=["http", "https"])


def get_organization_payment_settings(
    organization: Optional[Organization],
) -> Dict[str, str]:
    """Return only valid payment guidance saved for the resolved organization."""
    if organization is None:
        return {"instructions": "", "online_payment_url": ""}

    data = (
        OrganizationSettings.objects.filter(organization=organization)
        .values_list("data", flat=True)
        .first()
    )
    saved_settings = data.get("payment_settings") if isinstance(data, dict) else None
    if not isinstance(saved_settings, dict):
        saved_settings = {}

    instructions = saved_settings.get("instructions", "")
    instructions = instructions.strip() if isinstance(instructions, str) else ""
    online_payment_url = saved_settings.get("online_payment_url", "")
    online_payment_url = (
        online_payment_url.strip() if isinstance(online_payment_url, str) else ""
    )
    if online_payment_url:
        try:
            _PAYMENT_URL_VALIDATOR(online_payment_url)
        except ValidationError:
            online_payment_url = ""

    return {
        "instructions": instructions,
        "online_payment_url": online_payment_url,
    }


def update_organization_payment_settings(
    organization: Organization,
    *,
    instructions: str,
    online_payment_url: str,
) -> OrganizationSettings:
    """Save payment guidance without replacing unrelated organization settings."""
    org_settings, _ = OrganizationSettings.objects.get_or_create(
        organization=organization
    )
    if not isinstance(org_settings.data, dict):
        org_settings.data = {}

    payment_settings = org_settings.data.get("payment_settings")
    if not isinstance(payment_settings, dict):
        payment_settings = {}
    payment_settings.update(
        {
            "instructions": instructions.strip(),
            "online_payment_url": online_payment_url.strip(),
        }
    )
    org_settings.data["payment_settings"] = payment_settings
    org_settings.save(update_fields=["data", "updated_at"])
    return org_settings


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
    disallowed_hosts = {"*", "localhost", "127.0.0.1", ".".join(["0", "0", "0", "0"])}
    valid_hosts = [
        h
        for h in allowed_hosts
        if h and not h.startswith(".") and h not in disallowed_hosts
    ]
    if valid_hosts:
        return f"https://{valid_hosts[0]}"

    return ""


def _resolve_portal_url(raw_cfg: Dict[str, Any], org: Optional[Organization]) -> str:
    """Use the organization's canonical portal, not legacy email overrides."""
    return get_organization_url(org) if org else ""


def get_default_organization(slug: Optional[str] = None) -> Optional[Organization]:
    """Return the active organization for the given slug, or None if not found.

    If ``slug`` is provided, looks up by that slug.
    If ``slug`` is ``None``, uses the ``DEFAULT_ORGANIZATION_SLUG`` setting.
    Returns ``None`` when no matching active organization exists (instead of
    silently falling back to a hardcoded GoS row).
    """
    from organizations.models import Organization

    target_slug = (
        slug if slug is not None else getattr(settings, "DEFAULT_ORGANIZATION_SLUG", "")
    )
    if not target_slug:
        return None
    return Organization.objects.filter(slug__iexact=target_slug, is_active=True).first()


def get_organization_url(organization: Optional[Organization] = None) -> str:
    """Return the canonical public URL for an organization."""
    if not organization:
        return get_site_base_url()
    if not organization.pk or not organization.is_active:
        return ""

    root_domain = _configured_root_domain()
    active_domains = getattr(organization, "_active_portal_domains", None)
    if active_domains is None:
        active_domains = (
            OrganizationDomain.objects.for_organization(organization)
            .filter(status=OrganizationDomain.STATUS_ACTIVE)
            .order_by("created_at", "pk")
        )
    for domain in active_domains:
        if _hostname_routes_to_organization(organization, domain.hostname, root_domain):
            return f"https://{domain.hostname}"

    return get_organization_momentum_url(organization)


def get_organization_momentum_url(organization: Optional[Organization] = None) -> str:
    """Return an unambiguous Momentum subdomain URL for an organization."""
    if not organization:
        return get_site_base_url()
    if not organization.pk or not organization.is_active:
        return ""

    root_domain = _configured_root_domain()
    if not root_domain:
        return ""
    for label in dict.fromkeys((organization.subdomain, organization.slug)):
        if not is_valid_tenant_label(label):
            continue
        hostname = f"{str(label).lower()}.{root_domain}"
        if _hostname_routes_to_organization(organization, hostname, root_domain):
            return f"https://{hostname}"
    return ""


def _normalize_hostname(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    if "://" not in value:
        value = f"//{value}"
    try:
        hostname = urlsplit(value).hostname
    except ValueError:
        return ""
    return (hostname or "").rstrip(".").lower()


def _organization_custom_hostname(organization: Organization) -> str:
    return get_legacy_custom_hostname(getattr(organization, "settings", {}) or {})


_RESERVED_SUBDOMAIN_LABELS = {
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


def is_valid_tenant_label(label):
    label = str(label or "").lower()
    return label not in _RESERVED_SUBDOMAIN_LABELS and is_valid_domain_label(label)


def _configured_root_domain():
    return _normalize_hostname(
        getattr(settings, "MOMENTUM_ROOT_DOMAIN", "momentumteam.app")
    )


def _is_platform_hostname(host, root_domain):
    return host in {
        root_domain,
        f"www.{root_domain}" if root_domain else "",
        "momentumteam.app",
        "www.momentumteam.app",
        "localhost",
        "127.0.0.1",
    }


def _tenant_label_for_hostname(host, root_domain):
    for domain in {root_domain, "localhost"}:
        if domain and host.endswith(f".{domain}"):
            label = host[: -(len(domain) + 1)]
            if is_valid_tenant_label(label):
                return label
    return ""


def _hostname_claim_candidates(host, root_domain):
    label = _tenant_label_for_hostname(host, root_domain)
    query = models.Q(settings__has_key="custom_domain") | models.Q(
        settings__has_key="domain"
    )
    if host in LEGACY_ORGANIZATION_HOSTNAMES:
        query |= models.Q(slug="gos")
    if label:
        query |= models.Q(slug__iexact=label) | models.Q(subdomain__iexact=label)

    candidates = {}
    for organization in Organization.objects.filter(is_active=True).filter(query):
        if (
            _organization_custom_hostname(organization) == host
            or (host in LEGACY_ORGANIZATION_HOSTNAMES and organization.slug == "gos")
            or (
                label
                and label
                in {
                    str(organization.slug or "").lower(),
                    str(organization.subdomain or "").lower(),
                }
            )
        ):
            candidates[organization.pk] = organization
    return candidates


def _select_hostname_organization(host, registered_domain, candidates):
    candidates = dict(candidates)
    if registered_domain:
        if (
            not registered_domain.organization.is_active
            or registered_domain.status
            not in {
                OrganizationDomain.STATUS_PENDING_VERIFICATION,
                OrganizationDomain.STATUS_DNS_VERIFIED,
                OrganizationDomain.STATUS_ACTIVE,
            }
            or is_momentum_hostname(host)
            or (
                host in LEGACY_ORGANIZATION_HOSTNAMES
                and registered_domain.organization.slug != "gos"
            )
            or (
                registered_domain.status
                in {
                    OrganizationDomain.STATUS_DNS_VERIFIED,
                    OrganizationDomain.STATUS_ACTIVE,
                }
                and not registered_domain.dns_verified_at
            )
            or (
                registered_domain.status == OrganizationDomain.STATUS_ACTIVE
                and not registered_domain.activated_at
            )
        ):
            return None
        if registered_domain.status == OrganizationDomain.STATUS_ACTIVE:
            candidates[registered_domain.organization_id] = (
                registered_domain.organization
            )

    if len(candidates) != 1:
        return None
    organization = next(iter(candidates.values()))
    if registered_domain and registered_domain.organization_id != organization.pk:
        return None
    return organization


def _resolve_hostname(host, root_domain):
    if _is_platform_hostname(host, root_domain):
        return None
    registered_domain = (
        OrganizationDomain.objects.filter(hostname=host)
        .select_related("organization")
        .first()
    )
    return _select_hostname_organization(
        host, registered_domain, _hostname_claim_candidates(host, root_domain)
    )


def build_organization_routing_snapshot(organizations, registered_domains):
    """Build a query-free host-claim snapshot for an organization directory."""
    root_domain = _configured_root_domain()
    claims = {}

    def add_claim(host, organization):
        if host:
            claims.setdefault(host, {})[organization.pk] = organization

    for organization in organizations:
        if not organization.is_active:
            continue
        add_claim(_organization_custom_hostname(organization), organization)
        if organization.slug == "gos":
            for host in LEGACY_ORGANIZATION_HOSTNAMES:
                add_claim(host, organization)
        for label in (organization.subdomain, organization.slug):
            if is_valid_tenant_label(label):
                for domain in {root_domain, "localhost"}:
                    if domain:
                        add_claim(f"{str(label).lower()}.{domain}", organization)

    return (
        root_domain,
        claims,
        {domain.hostname: domain for domain in registered_domains},
    )


def _hostname_routes_to_organization(organization, host, root_domain):
    snapshot = getattr(organization, "_routing_snapshot", None)
    if snapshot is not None and snapshot[0] == root_domain:
        _, claims, domains = snapshot
        routed = None
        if not _is_platform_hostname(host, root_domain):
            routed = _select_hostname_organization(
                host, domains.get(host), claims.get(host, {})
            )
    else:
        routed = _resolve_hostname(host, root_domain)
    return bool(routed and routed.pk == organization.pk)


def hostname_has_conflicting_claims(domain):
    """Check whether a proposed activation conflicts with another tenant."""
    root_domain = _configured_root_domain()
    if (
        _is_platform_hostname(domain.hostname, root_domain)
        or is_momentum_hostname(domain.hostname)
        or (
            domain.hostname in LEGACY_ORGANIZATION_HOSTNAMES
            and domain.organization.slug != "gos"
        )
    ):
        return True
    return any(
        organization.pk != domain.organization_id
        for organization in _hostname_claim_candidates(
            domain.hostname, root_domain
        ).values()
    )


def resolve_organization_from_request(request) -> Optional[Organization]:
    """Resolve an active organization from the validated request hostname only."""
    if request is None:
        return None

    if hasattr(request, "_cached_organization"):
        return request._cached_organization

    # HttpRequest.get_host() enforces ALLOWED_HOSTS; do not trust raw Host metadata.
    host = _normalize_hostname(request.get_host())
    root_domain = _configured_root_domain()
    request._is_platform_domain = _is_platform_hostname(host, root_domain)

    # Keep Django's synthetic test host compatible with legacy tests only. It is
    # never used as a fallback for a real hostname.
    if host == "testserver" and getattr(settings, "TESTING", False):
        organization = get_default_organization()
        request._cached_organization = organization
        request._is_platform_domain = organization is None
        return organization

    if request._is_platform_domain:
        request._cached_organization = None
        return None

    organization = _resolve_hostname(host, root_domain)
    request._cached_organization = organization
    return organization


def get_organization_settings(
    organization: Optional[Organization] = None,
) -> SettingsDict:
    """Return the resolved SettingsDict for the given organization."""
    org = organization
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
    org = organization

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
    # Use neutral platform defaults only when no organization provides a value.
    # An organization name is the branding fallback for that tenant.
    default_reply_to = getattr(settings, "DEFAULT_REPLY_TO_EMAIL", "reply@example.com")
    default_from_email = getattr(settings, "NOTIFICATION_FROM_EMAIL", None) or getattr(
        settings, "DEFAULT_FROM_EMAIL", "no-reply@example.com"
    )

    sender_display_name = (
        raw_cfg.get("sender_display_name")
        or (dict.get(org.settings, "portal_title") if org else None)
        or (org.name if org else None)
        or default_name
        or "Platform Notifications"
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
    primary_color = raw_cfg.get("primary_color") or "#334155"
    secondary_color = raw_cfg.get("secondary_color") or "#f8f9fa"
    header_text_color = raw_cfg.get("header_text_color") or "#ffffff"
    logo_url = raw_cfg.get("logo_url") or ""
    if logo_url and logo_url.startswith("/"):
        base_url = get_site_base_url()
        if base_url:
            logo_url = f"{base_url}{logo_url}"

    def resolve_logo(key):
        value = raw_cfg.get(key) or ""
        base_url = get_site_base_url() if value.startswith("/") else ""
        return f"{base_url}{value}" if base_url else value

    wordmark_url = resolve_logo("wordmark_url")
    host_logo_url = resolve_logo("host_logo_url")
    organization_name = raw_cfg.get("organization_name") or (
        org.name if org else "Momentum"
    )
    address_line1 = raw_cfg.get("address_line1", "")
    address_line2 = raw_cfg.get("address_line2", "")
    tax_id = raw_cfg.get("tax_id", "")
    website = raw_cfg.get("website", "")
    footer_text = raw_cfg.get("footer_text", "")
    portal_url = lazy(_resolve_portal_url, str)(raw_cfg, org)

    contact_name = raw_cfg.get("contact_name", "")
    contact_email = raw_cfg.get("contact_email") or reply_to_email

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
        "wordmark_url": wordmark_url,
        "host_logo_url": host_logo_url,
        "organization_name": organization_name,
        "address_line1": address_line1,
        "address_line2": address_line2,
        "tax_id": tax_id,
        "website": website,
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
    previous = org_settings.data.get("email_settings") or {}
    email_settings = dict(email_settings)
    email_settings.pop("portal_url", None)
    if isinstance(previous, dict) and "portal_url" in previous:
        email_settings["portal_url"] = previous["portal_url"]
    org_settings.data["email_settings"] = email_settings
    org_settings.save(update_fields=["data", "updated_at"])
    return org_settings


def update_organization_settings(
    organization: Organization,
    new_settings: Dict[str, Any],
) -> OrganizationSettings:
    """Save settings into OrganizationSettings.data and update organization.settings."""
    org_settings, _ = OrganizationSettings.objects.get_or_create(
        organization=organization
    )
    if not isinstance(org_settings.data, dict):
        org_settings.data = {}
    org_settings.data.update(new_settings)
    org_settings.save(update_fields=["data", "updated_at"])

    if not isinstance(organization.settings, dict):
        organization.settings = {}
    organization.settings.update(new_settings)
    organization.save(update_fields=["settings", "updated_at"])
    return org_settings
