"""Sliding-window throttling for student and adult profile detail views."""

from __future__ import annotations

import logging
import math
import time

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)


class RateLimitCacheUnavailable(Exception):
    """Raised when profile rate-limit state cannot be checked safely."""


def check_profile_rate_limit(user_id, organization_id, *, profile_type="student"):
    """Return ``(allowed, retry_after_seconds)`` for one profile request.

    Each cache slot represents a request and expires independently, giving the
    user a rolling window while ``cache.add`` keeps slot acquisition atomic
    across workers using a shared cache backend.
    """
    if not getattr(settings, "PROFILE_RATE_LIMIT_ENABLED", False):
        return True, 0

    limit = int(getattr(settings, "PROFILE_RATE_LIMIT_COUNT", 10))
    window_seconds = int(getattr(settings, "PROFILE_RATE_LIMIT_WINDOW_SECONDS", 60))
    if limit < 1 or window_seconds < 1:
        raise RateLimitCacheUnavailable("Profile rate-limit settings are invalid")

    now = time.time()
    earliest_expiration = None
    key_prefix = f"{profile_type}_profile_rate_limit:{organization_id}:{user_id}"

    try:
        for slot in range(limit):
            cache_key = f"{key_prefix}:{slot}"
            expiration = now + window_seconds
            if cache.add(cache_key, expiration, timeout=window_seconds):
                return True, 0

            cached_expiration = cache.get(cache_key)
            if cached_expiration is None:
                # The slot may have expired between the failed add and this read.
                expiration = time.time() + window_seconds
                if cache.add(cache_key, expiration, timeout=window_seconds):
                    return True, 0
                cached_expiration = cache.get(cache_key)
                if cached_expiration is None:
                    continue

            cached_expiration = float(cached_expiration)
            if earliest_expiration is None or cached_expiration < earliest_expiration:
                earliest_expiration = cached_expiration
    except Exception as exc:
        raise RateLimitCacheUnavailable(
            "Profile rate-limit cache is unavailable"
        ) from exc

    retry_after = max(
        1,
        int(
            math.ceil(
                (earliest_expiration or time.time() + window_seconds) - time.time()
            )
        ),
    )
    logger.warning(
        "%s profile rate limit exceeded (user_id=%s, organization_id=%s, retry_after=%s)",
        profile_type.capitalize(),
        user_id,
        organization_id,
        retry_after,
    )
    return False, retry_after
