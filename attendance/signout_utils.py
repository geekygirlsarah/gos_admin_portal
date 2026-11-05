"""Lock/unlock helpers for digital sign-out stations."""

import hashlib

from django.conf import settings
from django.core import signing
from django.core.cache import cache
from django.core.signing import BadSignature, SignatureExpired

_COOKIE_MAX_AGE = 8 * 60 * 60  # 8 hours in seconds
_CODE_EXPIRY = 600  # 10 minutes
_CODE_REQUEST_LIMIT = 3
_CODE_REQUEST_WINDOW = 15 * 60
_CODE_REQUEST_IP_LIMIT = 20
_CODE_ATTEMPT_LIMIT = 5
_CODE_ATTEMPT_WINDOW = _CODE_EXPIRY
_COOKIE_SIGNING_SALT = "attendance.digital_signout_unlock"


def _cookie_name(config_id):
    return f"signout_unlocked_{config_id}"


def _make_unlock_token(config):
    return signing.dumps(
        {"config_id": config.pk, "organization_id": config.organization_id},
        salt=_COOKIE_SIGNING_SALT,
    )


def _is_unlocked(request, config):
    value = request.COOKIES.get(_cookie_name(config.pk))
    if not value:
        return False
    try:
        payload = signing.loads(
            value, salt=_COOKIE_SIGNING_SALT, max_age=_COOKIE_MAX_AGE
        )
    except (BadSignature, SignatureExpired, TypeError, ValueError):
        return False
    return payload == {
        "config_id": config.pk,
        "organization_id": config.organization_id,
    }


def _set_unlocked(response, config):
    response.set_cookie(
        _cookie_name(config.pk),
        _make_unlock_token(config),
        max_age=_COOKIE_MAX_AGE,
        httponly=True,
        secure=settings.SESSION_COOKIE_SECURE,
        samesite="Lax",
    )


def _clear_unlocked(response, config):
    response.delete_cookie(_cookie_name(config.pk))


def _email_digest(email):
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()


def _otp_cache_key(config, email):
    return f"signout_otp_{config.organization_id}_{config.pk}_{_email_digest(email)}"


def _otp_ready_cache_key(config, email, nonce):
    nonce_digest = hashlib.sha256(nonce.encode("utf-8")).hexdigest()
    return f"{_otp_cache_key(config, email)}_ready_{nonce_digest}"


def _rate_limit_cache_key(purpose, config, identity):
    digest = hashlib.sha256(str(identity).encode("utf-8")).hexdigest()
    return f"signout_{purpose}_{config.organization_id}_{config.pk}_{digest}"


def _increment_rate_limit(key, limit, window):
    """Atomically increment a shared-cache counter; fail closed on errors."""
    try:
        if cache.add(key, 1, timeout=window):
            return True
        return cache.incr(key) <= limit
    except Exception:
        return None


def _acquire_redemption_lock(config, email, nonce):
    key = f"{_otp_cache_key(config, email)}_redeemed_{nonce}"
    try:
        return cache.add(key, "1", timeout=_CODE_EXPIRY)
    except Exception:
        return None
