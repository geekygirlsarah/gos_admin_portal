import logging
import zoneinfo

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.shortcuts import redirect
from django.urls import Resolver404, resolve
from django.utils import timezone
from django.utils.deprecation import MiddlewareMixin

from applications.rate_limiting import (
    get_client_ip,
    rate_limit_hit,
    rate_limited_response,
)

logger = logging.getLogger(__name__)

EXEMPT_URL_NAMES = {
    "account_login",
    "account_logout",
    "account_signup",
    "account_confirm_email",
    "admin:login",
    "privacy_policy",
    "non_discrimination_policy",
    "health",
}

EXEMPT_PATH_PREFIXES = (
    "/accounts/",
    "/admin/",
    "/apply/",  # public application wizard
    "/guest/",  # public guest permission forms
    "/api/v1/",  # API endpoints use X-API-KEY header auth, not session auth
    "/kiosk/",  # public kiosk attendance sign-in pages
    "/health/",  # health check endpoint for infrastructure monitoring
    settings.MEDIA_URL,  # uploaded files (e.g., blank program documents linked from /apply/)
    settings.STATIC_URL,
)


class LoginRequiredMiddleware(MiddlewareMixin):
    """Redirect anonymous users to login for all pages except exempt ones."""

    def process_request(self, request):
        if request.user.is_authenticated:
            return None

        if self._is_exempt(request.path):
            return None

        return redirect(settings.LOGIN_URL + f"?next={request.get_full_path()}")

    def _is_exempt(self, path):
        # Allow exempt prefixes
        for prefix in EXEMPT_PATH_PREFIXES:
            if prefix and path.startswith(prefix):
                return True

        # Allow named urls in exempt set. Also try the trailing-slash variant:
        # an anonymous request to /health (no slash) would otherwise hit this
        # middleware before APPEND_SLASH gets a chance to normalize it, so
        # both /health and /health/ must be treated as exempt.
        for candidate in (path, path + "/"):
            try:
                match = resolve(candidate)
                if match.view_name in EXEMPT_URL_NAMES:
                    return True
            except Resolver404:
                # The candidate doesn't resolve to any known URL. Do NOT treat
                # it as exempt: an anonymous user hitting an unknown path should
                # still be redirected to login rather than being shown a bare
                # 404, which would otherwise leak information about which paths
                # exist.
                continue
            except Exception:
                logger.debug(
                    "Unexpected error resolving path %s", candidate, exc_info=True
                )
                continue
        return False


class TemporaryMentorBlockMiddleware(MiddlewareMixin):
    """TEMPORARY — remove once mentor portal features are ready.

    Enforces ``settings.MENTOR_ACCESS_BLOCKED`` (see the comment there for the
    full rationale). While the flag is on:
    - a user whose only role is Mentor (no Parent/Alumni flags and not a lead
      mentor) is logged out and redirected to the login page with a message;
    - a user who mentors AND is a parent/alumni stays logged in; their mentor
      role is suppressed by ``get_user_role`` so they only see non-mentor
      features;
    - lead mentors and superusers are unaffected.
    """

    MENTOR_BLOCK_MESSAGE = (
        "Sorry, mentors cannot log in yet. You'll receive an announcement when "
        "you can."
    )

    def process_request(self, request):
        if not getattr(settings, "MENTOR_ACCESS_BLOCKED", False):
            return None
        if not request.user.is_authenticated:
            return None

        from programs.permission_views import (
            user_is_alumni,
            user_is_mentor,
            user_is_parent,
        )

        if not user_is_mentor(request.user):
            return None
        # Lead mentors (and superusers) keep admin access.
        if (
            request.user.is_superuser
            or request.user.groups.filter(name="LeadMentor").exists()
        ):
            return None
        # Parents/alumni who also mentor may log in (mentor features are
        # suppressed elsewhere via get_user_role).
        if user_is_parent(request.user) or user_is_alumni(request.user):
            return None

        # Mentor is the user's only role: invalidate the session and bounce
        # them back to the login page with a message. The message is added
        # after logout() so it lands in the fresh session and survives the
        # redirect.
        logout(request)
        messages.error(request, self.MENTOR_BLOCK_MESSAGE)
        return redirect(settings.LOGIN_URL)


class ApplyRateLimitMiddleware(MiddlewareMixin):
    """Throttle POST requests to the public application wizard (/apply/).

    The wizard is exempt from :class:`LoginRequiredMiddleware`, so anonymous
    users can hit it directly. This caps each client IP at
    ``settings.APPLY_IP_POST_LIMIT`` POSTs per ``settings.APPLY_IP_POST_WINDOW_SECONDS``
    to blunt DoS and mass-application abuse. OTP-specific limits (per email /
    per application) are enforced in the wizard views themselves.
    """

    def process_request(self, request):
        if not getattr(settings, "APPLY_RATE_LIMIT_ENABLED", True):
            return None
        if request.method != "POST" or not request.path.startswith("/apply/"):
            return None

        allowed, retry_after = rate_limit_hit(
            "ip",
            get_client_ip(request),
            getattr(settings, "APPLY_IP_POST_LIMIT", 10),
            getattr(settings, "APPLY_IP_POST_WINDOW_SECONDS", 60),
        )
        if not allowed:
            return rate_limited_response(request, retry_after)
        return None


class TimezoneMiddleware(MiddlewareMixin):
    """
    Middleware to activate the user's preferred timezone from the session.
    """

    def process_request(self, request):
        tzname = request.session.get("django_timezone")
        if tzname:
            try:
                timezone.activate(zoneinfo.ZoneInfo(tzname))
            except Exception:
                timezone.deactivate()
        else:
            timezone.deactivate()
