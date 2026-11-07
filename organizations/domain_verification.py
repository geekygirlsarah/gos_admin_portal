import hmac

import dns.exception
import dns.resolver

from organizations.domain_utils import DOMAIN_TXT_PREFIX


def verify_domain_txt(domain):
    """Check whether DNS publishes the exact TXT challenge for a domain."""
    record_name = f"{DOMAIN_TXT_PREFIX}.{domain.hostname}"
    try:
        records = dns.resolver.resolve(record_name, "TXT", lifetime=3.0)
    except (dns.exception.DNSException, OSError):
        return False

    expected = domain.verification_txt_value.encode("ascii")
    for record in records:
        try:
            value = b"".join(record.strings).strip()
        except (AttributeError, TypeError):
            continue
        if hmac.compare_digest(value, expected):
            return True
    return False
