import ipaddress
import re
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ValidationError

LEGACY_ORGANIZATION_HOSTNAMES = {
    "portal.girlsofsteelrobotics.org",
    "girlsofsteelrobotics.org",
    "gosadmin.com",
}

DOMAIN_TXT_PREFIX = "_momentum-verification"
_DOMAIN_LABEL_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def is_valid_domain_label(label):
    return bool(_DOMAIN_LABEL_PATTERN.fullmatch(str(label or "").lower()))


def normalize_domain_hostname(value):
    """Normalize a user-entered hostname to a lower-case ASCII DNS name."""
    hostname = str(value or "").strip()
    if not hostname or any(char in hostname for char in ":/@?#\\"):
        raise ValidationError("Enter a hostname only, without a URL, path, or port.")

    hostname = hostname.rstrip(".")
    try:
        hostname = hostname.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ValidationError("Enter a valid DNS hostname.") from exc

    if len(hostname) > 253 or "." not in hostname:
        raise ValidationError("Enter a fully qualified domain name.")
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise ValidationError("IP addresses cannot be registered as portal domains.")

    labels = hostname.split(".")
    if any(not is_valid_domain_label(label) for label in labels):
        raise ValidationError("Enter a valid DNS hostname.")
    return hostname


def get_legacy_custom_hostname(organization_settings):
    """Read an existing custom hostname for temporary routing compatibility."""
    if not isinstance(organization_settings, dict):
        return ""
    custom_domain = organization_settings.get("custom_domain") or ""
    if isinstance(custom_domain, dict):
        if custom_domain.get("enabled") is False:
            return ""
        custom_domain = custom_domain.get("domain") or ""
    if not custom_domain:
        custom_domain = organization_settings.get("domain") or ""
        if isinstance(custom_domain, dict):
            if custom_domain.get("enabled") is False:
                return ""
            custom_domain = custom_domain.get("domain") or ""
    if not custom_domain:
        return ""

    value = str(custom_domain).strip()
    if "://" in value:
        try:
            value = urlsplit(value).hostname or ""
        except ValueError:
            return ""
    else:
        value = value.split("/", 1)[0].split(":", 1)[0]
    try:
        return normalize_domain_hostname(value)
    except ValidationError:
        return ""


def is_momentum_hostname(hostname):
    """Whether a hostname belongs to the platform's reserved tenant namespace."""
    root_domain = str(getattr(settings, "MOMENTUM_ROOT_DOMAIN", "localhost") or "")
    try:
        root_domain = normalize_domain_hostname(root_domain)
    except ValidationError:
        root_domain = ""
    return bool(
        hostname in {root_domain, "localhost"}
        or hostname.endswith(".localhost")
        or (root_domain and hostname.endswith(f".{root_domain}"))
    )
