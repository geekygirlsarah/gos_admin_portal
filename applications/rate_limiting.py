"""Throttling for the public application wizard (/apply/).

The wizard is exempt from the login-required middleware, so anonymous users
can hit it directly. These helpers cap how often an IP can POST to the wizard
and how often a single email/application can request or verify OTP codes,
blunting DoS, mass-application and email-harvesting abuse.

Counters live in Django's cache framework so they persist between requests
(and, when a shared cache backend is configured, across app servers). Limits
are read from settings at call time so ``override_settings`` works in tests.
"""

from __future__ import annotations

import math
import time

from django.conf import settings
from django.shortcuts import render


def get_client_ip(request):
    """Best-effort client IP, honoring X-Forwarded-For when behind a proxy."""
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR") or "unknown"


def _cache_key(scope: str, key: str) -> str:
    return f"gos_rate_limit:{scope}:{key}"


def rate_limit_hit(scope: str, key: str, limit: int, window_seconds: int):
    """Record one hit against ``scope/key`` and report whether it's allowed.

    Returns ``(allowed, retry_after_seconds)``. ``allowed`` is ``False`` once
    more than ``limit`` hits land inside ``window_seconds``; in that case
    ``retry_after_seconds`` is how long until the window resets.
    """
    from django.core.cache import cache

    cache_key = _cache_key(scope, key)
    now = time.time()
    data = cache.get(cache_key)
    if data is None or now - data["start"] >= window_seconds:
        data = {"start": now, "count": 0}
    data["count"] += 1
    cache.set(cache_key, data, window_seconds)

    if data["count"] <= limit:
        return True, 0

    elapsed = now - data["start"]
    retry_after = max(1, int(math.ceil(window_seconds - elapsed)))
    return False, retry_after


def _friendly_duration(seconds: int) -> str:
    """Turn a retry-after countdown into a friendly human phrase."""
    seconds = max(1, int(seconds))
    if seconds >= 60:
        minutes = int(round(seconds / 60))
        if minutes == 1:
            return "about a minute"
        return f"about {minutes} minutes"
    if seconds == 1:
        return "about a second"
    return f"about {seconds} seconds"


def rate_limited_response(request, retry_after: int):
    """Build a 429 response with a Retry-After header and a friendly page."""
    response = render(
        request,
        "429.html",
        {
            "retry_after": retry_after,
            "retry_after_text": _friendly_duration(retry_after),
        },
        status=429,
    )
    response["Retry-After"] = str(retry_after)
    return response


def check_rate_limit(request, scope: str, key: str, limit: int, window_seconds: int):
    """Return a 429 response if the limit is exceeded, else ``None``.

    No-op (returns ``None``) when throttling is disabled (the test suite).
    """
    if not getattr(settings, "APPLY_RATE_LIMIT_ENABLED", True):
        return None
    allowed, retry_after = rate_limit_hit(scope, key, limit, window_seconds)
    if allowed:
        return None
    return rate_limited_response(request, retry_after)


def check_otp_send_limit(request, email: str):
    """Limit OTP code requests to ``APPLY_OTP_SEND_LIMIT`` per email / hour."""
    email = (email or "").strip().lower()
    if not email:
        return None
    return check_rate_limit(
        request,
        "otp_send",
        email,
        getattr(settings, "APPLY_OTP_SEND_LIMIT", 5),
        getattr(settings, "APPLY_OTP_WINDOW_SECONDS", 3600),
    )


def check_otp_verify_limit(request, application_id: str):
    """Limit OTP verify attempts to ``APPLY_OTP_VERIFY_LIMIT`` per app / hour."""
    application_id = (application_id or "").strip().upper()
    if not application_id:
        return None
    return check_rate_limit(
        request,
        "otp_verify",
        application_id,
        getattr(settings, "APPLY_OTP_VERIFY_LIMIT", 10),
        getattr(settings, "APPLY_OTP_WINDOW_SECONDS", 3600),
    )
