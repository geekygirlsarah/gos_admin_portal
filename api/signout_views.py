import json
import logging

from allauth.account.adapter import get_adapter
from django.core.cache import cache
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from attendance.signout_utils import _clear_unlocked, _set_unlocked
from attendance.signout_views import _get_config_or_404
from GoSAdminPortal.adapter import _find_or_provision_user_for_email

logger = logging.getLogger(__name__)

_CODE_EXPIRY = 600  # 10 minutes


@require_POST
def signout_request_code(request, config_id):
    """POST /api/v1/signout/<id>/request_code/
    Accepts JSON {"email": "..."}.
    Generates a 6-digit code, stores it in cache, and sends it via email.
    """
    _get_config_or_404(config_id)
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"success": False, "error": "Invalid JSON."}, status=400)

    email = body.get("email", "").strip().lower()
    if not email:
        return JsonResponse(
            {"success": False, "error": "Email is required."}, status=400
        )

    if not _find_or_provision_user_for_email(email):
        return JsonResponse(
            {
                "success": False,
                "error": "This email is not authorized to unlock sign-out stations.",
            },
            status=403,
        )

    adapter = get_adapter()
    code = adapter.generate_login_code()

    cache_key = f"signout_otp_{config_id}_{email}"
    cache.set(cache_key, code, _CODE_EXPIRY)

    adapter.send_mail("account/email/login_code", email, {"code": code})

    return JsonResponse({"success": True})


@require_POST
def signout_unlock(request, config_id):
    """POST /api/v1/signout/<id>/unlock/
    Accepts JSON {"email": "...", "code": "..."}.
    Verifies the code and sets a HttpOnly cookie to unlock the sign-out station for 8 hours.
    """
    _get_config_or_404(config_id)
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"success": False, "error": "Invalid JSON."}, status=400)

    email = body.get("email", "").strip().lower()
    code = body.get("code", "").strip()

    if not email or not code:
        return JsonResponse(
            {"success": False, "error": "Email and code are required."}, status=400
        )

    cache_key = f"signout_otp_{config_id}_{email}"
    stored_code = cache.get(cache_key)

    if not stored_code or stored_code != code:
        return JsonResponse(
            {"success": False, "error": "Invalid or expired code."}, status=403
        )

    cache.delete(cache_key)

    response = JsonResponse({"success": True})
    _set_unlocked(response, config_id)
    return response


@require_POST
def signout_lock(request, config_id):
    """POST /api/v1/signout/<id>/lock/
    Clears the unlock cookie for the sign-out station.
    """
    _get_config_or_404(config_id)
    response = JsonResponse({"success": True})
    _clear_unlocked(response, config_id)
    return response
