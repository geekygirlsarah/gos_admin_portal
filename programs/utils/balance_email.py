"""Branding and sender defaults for tenant-safe balance statements."""

from django.conf import settings

from organizations.models import OrganizationSettings
from organizations.utils import get_organization_email_settings


def get_balance_email_settings(organization):
    if organization is None:
        raise ValueError("An organization is required for balance emails.")

    config = get_organization_email_settings(organization).copy()
    data = (
        OrganizationSettings.objects.filter(organization=organization)
        .values_list("data", flat=True)
        .first()
        or {}
    )
    raw = data.get("email_settings", {}) if isinstance(data, dict) else {}
    if not isinstance(raw, dict):
        raw = {}

    config["organization_name"] = raw.get("organization_name") or "Momentum"
    config["sender_display_name"] = (
        raw.get("sender_display_name") or raw.get("organization_name") or "Momentum"
    )
    config["primary_color"] = raw.get("primary_color") or "#1e293b"
    config["secondary_color"] = raw.get("secondary_color") or "#f8f9fa"
    config["header_text_color"] = raw.get("header_text_color") or "#ffffff"
    for field in ("address_line1", "address_line2", "contact_name", "footer_text"):
        config[field] = raw.get(field) or ""
    config["contact_email"] = (
        raw.get("contact_email") or raw.get("reply_to_email") or ""
    )
    config["portal_url"] = str(config.get("portal_url") or "")
    return config


def get_balance_email_from_address(config):
    address = config.get("notification_from_email") or settings.DEFAULT_FROM_EMAIL
    name = config.get("sender_display_name")
    if name and "<" not in address:
        return f'"{name}" <{address}>'
    return address
