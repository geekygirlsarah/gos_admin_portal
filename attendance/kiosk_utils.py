import hashlib

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.signing import BadSignature, SignatureExpired
from django.http import Http404

from organizations.models import UserOrganizationMembership

from .models import KioskConfig

_COOKIE_MAX_AGE = 7 * 24 * 60 * 60  # 7 days in seconds
_CODE_EXPIRY = 600  # 10 minutes
_CODE_REQUEST_LIMIT = 3
_CODE_REQUEST_WINDOW = 15 * 60
_CODE_REQUEST_IP_LIMIT = 20
_CODE_ATTEMPT_LIMIT = 5
_CODE_ATTEMPT_WINDOW = _CODE_EXPIRY
_COOKIE_SIGNING_SALT = "attendance.kiosk_unlock"


def _cookie_name(kiosk_id):
    return f"kiosk_unlocked_{kiosk_id}"


def _get_kiosk_or_404(kiosk_id, organization):
    if organization is None or not organization.is_active:
        raise Http404("Kiosk not found or inactive.")
    try:
        return KioskConfig.objects.select_related("program", "organization").get(
            pk=kiosk_id,
            organization=organization,
            program__organization=organization,
            is_active=True,
        )
    except KioskConfig.DoesNotExist:
        raise Http404("Kiosk not found or inactive.")


def _make_unlock_token(kiosk):
    return signing.dumps(
        {"kiosk_id": kiosk.pk, "organization_id": kiosk.organization_id},
        salt=_COOKIE_SIGNING_SALT,
    )


def _is_unlocked(request, kiosk):
    value = request.COOKIES.get(_cookie_name(kiosk.pk))
    if not value:
        return False
    try:
        payload = signing.loads(
            value, salt=_COOKIE_SIGNING_SALT, max_age=_COOKIE_MAX_AGE
        )
    except (BadSignature, SignatureExpired, TypeError, ValueError):
        return False
    return payload == {
        "kiosk_id": kiosk.pk,
        "organization_id": kiosk.organization_id,
    }


def _get_eligible_unlock_user(email, organization):
    """Return the one active account authorized to unlock this tenant's kiosk."""
    if not email or organization is None or not organization.is_active:
        return None

    User = get_user_model()
    matching_users = list(User.objects.filter(email__iexact=email).order_by("pk")[:2])
    if len(matching_users) != 1:
        return None
    user = matching_users[0]
    if not user.is_active:
        return None
    if user.is_superuser:
        return user

    active_membership = UserOrganizationMembership.objects.filter(
        user=user,
        organization=organization,
        organization__is_active=True,
        is_active=True,
        role__in=("LeadMentor", "Mentor"),
    ).exists()
    if not active_membership:
        return None

    from programs.permission_views import user_is_lead_mentor, user_is_mentor
    from programs.utils import active_mentors

    if user_is_lead_mentor(user, org=organization):
        return user
    if (
        user_is_mentor(user, org=organization)
        and active_mentors(organization=organization).filter(user=user).exists()
    ):
        return user
    return None


def _email_digest(email):
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()


def _otp_cache_key(kiosk, email):
    return f"kiosk_otp_{kiosk.organization_id}_{kiosk.pk}_" f"{_email_digest(email)}"


def _otp_ready_cache_key(kiosk, email, nonce):
    nonce_digest = hashlib.sha256(nonce.encode("utf-8")).hexdigest()
    return f"{_otp_cache_key(kiosk, email)}_ready_{nonce_digest}"


def _rate_limit_cache_key(purpose, kiosk, identity):
    digest = hashlib.sha256(str(identity).encode("utf-8")).hexdigest()
    return f"kiosk_{purpose}_{kiosk.organization_id}_{kiosk.pk}_{digest}"


def _increment_rate_limit(key, limit, window):
    """Atomically increment a shared-cache counter; fail closed on cache errors."""
    from django.core.cache import cache

    try:
        if cache.add(key, 1, timeout=window):
            return True
        return cache.incr(key) <= limit
    except Exception:
        return None


def _acquire_redemption_lock(kiosk, email, nonce):
    from django.core.cache import cache

    key = _otp_cache_key(kiosk, email) + f"_redeeming_{nonce}"
    try:
        return cache.add(key, "1", timeout=_CODE_EXPIRY)
    except Exception:
        return None


def _unlock_cookie_options():
    return {
        "max_age": _COOKIE_MAX_AGE,
        "httponly": True,
        "secure": settings.SESSION_COOKIE_SECURE,
        "samesite": "Lax",
    }
