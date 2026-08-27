import logging
import zoneinfo

from django.conf import settings
from django.shortcuts import redirect
from django.urls import Resolver404, resolve, reverse
from django.utils import timezone

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
    "mentor_agreement",
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


class LoginRequiredMiddleware:
    """Redirect anonymous users to login for all pages except exempt ones."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated or self._is_exempt(request.path):
            return self.get_response(request)
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


class ApplyRateLimitMiddleware:
    """Throttle POST requests to the public application wizard (/apply/).

    The wizard is exempt from :class:`LoginRequiredMiddleware`, so anonymous
    users can hit it directly. This caps each client IP at
    ``settings.APPLY_IP_POST_LIMIT`` POSTs per ``settings.APPLY_IP_POST_WINDOW_SECONDS``
    to blunt DoS and mass-application abuse. OTP-specific limits (per email /
    per application) are enforced in the wizard views themselves.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if getattr(settings, "APPLY_RATE_LIMIT_ENABLED", True) and (
            request.method == "POST" and request.path.startswith("/apply/")
        ):
            allowed, retry_after = rate_limit_hit(
                "ip",
                get_client_ip(request),
                getattr(settings, "APPLY_IP_POST_LIMIT", 10),
                getattr(settings, "APPLY_IP_POST_WINDOW_SECONDS", 60),
            )
            if not allowed:
                return rate_limited_response(request, retry_after)
        return self.get_response(request)


class TimezoneMiddleware:
    """
    Middleware to activate the user's preferred timezone from the session.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        tzname = request.session.get("django_timezone")
        if tzname:
            try:
                timezone.activate(zoneinfo.ZoneInfo(tzname))
            except Exception:
                timezone.deactivate()
        else:
            timezone.deactivate()
        return self.get_response(request)


class MentorAgreementMiddleware:
    """Redirect mentors who have not accepted all current agreements.

    Runs after LoginRequiredMiddleware (so the user is already authenticated).
    Any authenticated user with
    ``is_mentor=True`` (or in the LeadMentor group, or a superuser) who has
    not accepted every active :class:`MentorAgreement` is redirected to
    the agreement page.  This applies regardless of other roles (e.g.
    parent+mentor combos) — the policy covers everyone with mentor access.

    Controlled by ``settings.MENTOR_AGREEMENT_ENABLED`` (disabled during
    tests by default).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not getattr(settings, "MENTOR_AGREEMENT_ENABLED", True):
            return self.get_response(request)
        if not request.user.is_authenticated:
            return self.get_response(request)
        # Don't redirect away from the agreement page itself.
        if request.path.startswith("/mentor-agreement/"):
            return self.get_response(request)

        # Skip non-portal paths (media, static, admin, API, etc.) so that
        # file downloads, static assets, and admin pages stay accessible.
        for prefix in EXEMPT_PATH_PREFIXES:
            if prefix and request.path.startswith(prefix):
                return self.get_response(request)

        # Only apply to users who have a mentor role.
        from programs.permission_views import user_is_mentor

        if not user_is_mentor(request.user):
            return self.get_response(request)

        from programs.models import MentorAgreementAcceptance

        if MentorAgreementAcceptance.has_accepted_for_user(request.user):
            return self.get_response(request)

        # User has not accepted — redirect to the agreement page.
        next_url = request.get_full_path()
        return redirect(reverse("mentor_agreement") + f"?next={next_url}")
