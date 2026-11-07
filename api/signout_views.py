import hmac
import json
import logging
import secrets

from allauth.account.adapter import get_adapter
from django.core.cache import cache
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from attendance.kiosk_utils import _get_eligible_unlock_user
from attendance.signout_utils import (
    _CODE_ATTEMPT_LIMIT,
    _CODE_ATTEMPT_WINDOW,
    _CODE_EXPIRY,
    _CODE_REQUEST_IP_LIMIT,
    _CODE_REQUEST_LIMIT,
    _CODE_REQUEST_WINDOW,
    _acquire_redemption_lock,
    _clear_unlocked,
    _increment_rate_limit,
    _otp_cache_key,
    _otp_ready_cache_key,
    _rate_limit_cache_key,
    _set_unlocked,
)
from attendance.signout_views import _get_config_or_404

logger = logging.getLogger(__name__)


@require_POST
def signout_request_code(request, config_id):
    """POST /api/v1/signout/<id>/request_code/
    Accepts JSON {"email": "..."}.
    Generates a 6-digit code, stores it in cache, and sends it via email.
    """
    config = _get_config_or_404(config_id, getattr(request, "organization", None))
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, TypeError, ValueError):
        return JsonResponse({"success": False, "error": "Invalid JSON."}, status=400)

    if not isinstance(body, dict):
        return JsonResponse({"success": False, "error": "Invalid JSON."}, status=400)
    email = body.get("email", "")
    if not isinstance(email, str):
        email = ""
    email = email.strip().lower()
    if not email:
        return JsonResponse(
            {"success": False, "error": "Email is required."}, status=400
        )

    ip_address = request.META.get("REMOTE_ADDR", "")
    for key, limit in (
        (
            _rate_limit_cache_key("request_ip", config, ip_address),
            _CODE_REQUEST_IP_LIMIT,
        ),
        (
            _rate_limit_cache_key("request_email", config, email),
            _CODE_REQUEST_LIMIT,
        ),
    ):
        allowed = _increment_rate_limit(key, limit, _CODE_REQUEST_WINDOW)
        if allowed is None:
            return JsonResponse(
                {"success": False, "error": "Temporarily unavailable."},
                status=503,
            )
        if not allowed:
            return JsonResponse(
                {"success": False, "error": "Too many code requests."}, status=429
            )

    user = _get_eligible_unlock_user(email, config.organization)
    if user is None:
        return JsonResponse(
            {
                "success": False,
                "error": "This email is not authorized to unlock sign-out stations.",
            },
            status=403,
        )

    adapter = get_adapter()
    code = adapter.generate_login_code()
    nonce = secrets.token_urlsafe(16)
    cache_key = _otp_cache_key(config, email)
    ready_key = _otp_ready_cache_key(config, email, nonce)
    record = {"code": code, "nonce": nonce, "user_id": user.pk}

    try:
        cache.set(cache_key, record, _CODE_EXPIRY)
        adapter.send_mail("account/email/login_code", user.email, {"code": code})
        if not cache.add(ready_key, True, _CODE_EXPIRY):
            raise RuntimeError("Unable to mark the sign-out code ready.")
    except Exception:
        logger.warning("Unable to send a digital sign-out unlock code.")
        return JsonResponse(
            {"success": False, "error": "Unable to send an unlock code."},
            status=503,
        )

    return JsonResponse({"success": True})


@require_POST
def signout_unlock(request, config_id):
    """POST /api/v1/signout/<id>/unlock/
    Accepts JSON {"email": "...", "code": "..."}.
    Verifies the code and sets a HttpOnly cookie to unlock the sign-out station for 8 hours.
    """
    config = _get_config_or_404(config_id, getattr(request, "organization", None))
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, TypeError, ValueError):
        return JsonResponse({"success": False, "error": "Invalid JSON."}, status=400)

    if not isinstance(body, dict):
        return JsonResponse({"success": False, "error": "Invalid JSON."}, status=400)
    email = body.get("email", "")
    code = body.get("code", "")
    if not isinstance(email, str):
        email = ""
    if not isinstance(code, str):
        code = ""
    email = email.strip().lower()
    code = code.strip()

    if not email or not code:
        return JsonResponse(
            {"success": False, "error": "Email and code are required."}, status=400
        )

    ip_address = request.META.get("REMOTE_ADDR", "")
    for key, limit in (
        (
            _rate_limit_cache_key("attempt_ip", config, ip_address),
            _CODE_ATTEMPT_LIMIT * 5,
        ),
        (
            _rate_limit_cache_key("attempt_email", config, email),
            _CODE_ATTEMPT_LIMIT,
        ),
    ):
        allowed = _increment_rate_limit(key, limit, _CODE_ATTEMPT_WINDOW)
        if allowed is None:
            return JsonResponse(
                {"success": False, "error": "Temporarily unavailable."},
                status=503,
            )
        if not allowed:
            return JsonResponse(
                {"success": False, "error": "Too many code attempts."}, status=429
            )

    user = _get_eligible_unlock_user(email, config.organization)
    if user is None:
        return JsonResponse(
            {"success": False, "error": "Invalid or expired code."}, status=403
        )

    cache_key = _otp_cache_key(config, email)
    try:
        record = cache.get(cache_key)
    except Exception:
        return JsonResponse(
            {"success": False, "error": "Temporarily unavailable."}, status=503
        )

    if (
        not isinstance(record, dict)
        or not isinstance(record.get("code"), str)
        or not isinstance(record.get("nonce"), str)
        or record.get("user_id") != user.pk
        or not code.isascii()
        or not code.isdigit()
        or len(code) != 6
        or not hmac.compare_digest(record["code"], code)
    ):
        return JsonResponse(
            {"success": False, "error": "Invalid or expired code."}, status=403
        )

    ready_key = _otp_ready_cache_key(config, email, record["nonce"])
    try:
        if cache.get(ready_key) is not True:
            return JsonResponse(
                {"success": False, "error": "Invalid or expired code."}, status=403
            )
    except Exception:
        return JsonResponse(
            {"success": False, "error": "Temporarily unavailable."}, status=503
        )

    lock = _acquire_redemption_lock(config, email, record["nonce"])
    if lock is None:
        return JsonResponse(
            {"success": False, "error": "Temporarily unavailable."}, status=503
        )
    if not lock:
        return JsonResponse(
            {"success": False, "error": "Invalid or expired code."}, status=403
        )

    try:
        current_record = cache.get(cache_key)
        ready = cache.get(ready_key)
    except Exception:
        return JsonResponse(
            {"success": False, "error": "Temporarily unavailable."}, status=503
        )
    current_user = _get_eligible_unlock_user(email, config.organization)
    if (
        current_record != record
        or ready is not True
        or current_user is None
        or current_user.pk != user.pk
    ):
        return JsonResponse(
            {"success": False, "error": "Invalid or expired code."}, status=403
        )

    response = JsonResponse({"success": True})
    _set_unlocked(response, config)
    return response


@require_POST
def signout_lock(request, config_id):
    """POST /api/v1/signout/<id>/lock/
    Clears the unlock cookie for the sign-out station.
    """
    config = _get_config_or_404(config_id, getattr(request, "organization", None))
    response = JsonResponse({"success": True})
    _clear_unlocked(response, config)
    return response
