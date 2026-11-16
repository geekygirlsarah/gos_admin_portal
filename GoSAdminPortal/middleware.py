import logging

from django.conf import settings
from django.http import (
    Http404,
    HttpResponse,
    HttpResponseForbidden,
    HttpResponseNotFound,
)
from django.shortcuts import redirect
from django.template.loader import render_to_string
from django.urls import Resolver404, resolve, reverse

from applications.rate_limiting import (
    get_client_ip,
    rate_limit_hit,
    rate_limited_response,
)

logger = logging.getLogger(__name__)


def _default_organization():
    """Return the configured default organization, or ``None`` when unset."""
    from organizations.models import Organization

    slug = getattr(settings, "DEFAULT_ORGANIZATION_SLUG", "")
    if not slug:
        return None
    return Organization.objects.filter(slug=slug).first()


class MaintenanceModeMiddleware:
    """Show a standalone maintenance page before session or tenant access."""

    HEALTH_PATHS = frozenset({"/health", "/health/"})

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if (
            not getattr(settings, "MAINTENANCE_MODE", False)
            or request.path in self.HEALTH_PATHS
            or (settings.STATIC_URL and request.path.startswith(settings.STATIC_URL))
        ):
            return self.get_response(request)

        response = HttpResponse(
            render_to_string("maintenance.html"),
            status=503,
        )
        response["Cache-Control"] = (
            "private, no-store, no-cache, max-age=0, must-revalidate"
        )
        response["Pragma"] = "no-cache"
        response["Expires"] = "0"
        return response


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
    "tosend_webhook",
    "api_tosend_webhook",
    "landing",
    "about",
    "organizations:support_impersonation_handoff",
    "organizations:support_impersonation_redeem",
    "organizations:support_impersonation_resume",
    "organizations:support_impersonation_confirm",
}

EXEMPT_PATH_PREFIXES = (
    "/accounts/",
    "/admin/",
    "/platform-admin/",
    "/apply/",  # public application wizard
    "/guest/",  # public guest permission forms
    "/signout/",  # public digital sign-out pages
    "/api/v1/",  # API endpoints use X-API-KEY header auth, not session auth
    "/api/webhooks/",  # public webhook endpoints (toSend, etc.)
    "/programs/webhooks/",  # public webhook endpoints
    "/kiosk/",  # public kiosk attendance sign-in pages
    "/calendar/",  # public calendar + ICS feeds (public feeds only for anon)
    "/organizations/invitations/",  # public / member invitation links
    "/health/",  # health check endpoint for infrastructure monitoring
    settings.MEDIA_URL,  # uploaded files (e.g., blank program documents linked from /apply/)
    settings.STATIC_URL,
)


class LoginRequiredMiddleware:
    """Redirect anonymous users to login for all pages except exempt ones."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Check exemption BEFORE touching request.user: reading the lazy user
        # loads the session from the DB, and exempt file paths (e.g. a photo
        # grid's concurrent /media/ GETs) must stay DB-free — that pattern
        # exhausted Postgres connection slots in production.
        if self._is_exempt(request.path, request) or request.user.is_authenticated:
            return self.get_response(request)
        return redirect(settings.LOGIN_URL + f"?next={request.get_full_path()}")

    def _is_exempt(self, path, request=None):
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
                # The root URL on the root platform (request.organization is None)
                # serves the public marketing landing page.
                if (
                    match.view_name == "home"
                    and request is not None
                    and getattr(request, "organization", None) is None
                ):
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


class TenantMembershipGuardMiddleware:
    """Require active host-organization membership for authenticated users."""

    EXEMPT_URL_NAMES = {
        "account_login",
        "account_logout",
        "account_signup",
        "account_confirm_email",
        "account_email_verification_sent",
        "account_request_login_code",
        "account_confirm_login_code",
        "account_reset_password",
        "account_reset_password_done",
        "account_reset_password_from_key",
        "account_reset_password_from_key_done",
        "account_confirm_password_reset_code",
        "account_complete_password_reset",
        "account_password_reset_completed",
        "organizations:accept_invitation",
        "organizations:support_impersonation_handoff",
        "organizations:support_impersonation_redeem",
        "organizations:support_impersonation_resume",
        "organizations:support_impersonation_confirm",
        "platform_admin:impersonate_stop",
        "tosend_webhook",
        "api_tosend_webhook",
    }

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        if (
            (settings.STATIC_URL and path.startswith(settings.STATIC_URL))
            or (settings.MEDIA_URL and path.startswith(settings.MEDIA_URL))
            or path in ("/health", "/health/")
            or getattr(request, "organization", None) is None
        ):
            return self.get_response(request)

        for candidate in (path, path + "/"):
            try:
                if resolve(candidate).view_name in self.EXEMPT_URL_NAMES:
                    return self.get_response(request)
            except Resolver404:
                continue

        user = request.user
        if not user.is_authenticated or user.is_superuser:
            return self.get_response(request)

        from organizations.models import UserOrganizationMembership

        if UserOrganizationMembership.objects.filter(
            user=user, organization=request.organization, is_active=True
        ).exists():
            return self.get_response(request)
        return HttpResponseForbidden("Forbidden")


class OrganizationMiddleware:
    """Attach the active organization to every request as ``request.organization``.

    Resolves the organization from the validated request hostname.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = getattr(request, "path", "")
        if (settings.MEDIA_URL and path.startswith(settings.MEDIA_URL)) or (
            settings.STATIC_URL and path.startswith(settings.STATIC_URL)
        ):
            request.organization = None
            return self.get_response(request)

        from organizations.utils import resolve_organization_from_request

        request.organization = resolve_organization_from_request(request)
        return self.get_response(request)


class RootDomainGuardMiddleware:
    """Keep tenant data unreachable when no organization is resolved.

    The root platform domain has no organization context: it serves the
    marketing site, auth, and platform-admin tooling only. Unknown hosts are
    rejected before views can run without tenant scoping.
    """

    ALLOWED_PREFIXES = (
        "/accounts/",
        "/admin/",
        "/platform-admin/",
        "/health",
        "/api/webhooks/",
        "/landing/",
        "/about/",
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        is_static_or_media = (
            settings.MEDIA_URL and path.startswith(settings.MEDIA_URL)
        ) or (settings.STATIC_URL and path.startswith(settings.STATIC_URL))
        user = getattr(request, "user", None)
        if (
            getattr(request, "organization", None) is None
            and getattr(request, "_is_platform_domain", False)
            and path == reverse("notification_list")
            and request.method in {"GET", "HEAD"}
            and getattr(user, "is_authenticated", False)
            and getattr(user, "is_active", False)
            and getattr(user, "is_superuser", False)
        ):
            return redirect("platform_admin:notification_list")

        if (
            getattr(request, "organization", None) is None
            and not getattr(request, "_is_platform_domain", False)
            and not is_static_or_media
        ):
            return HttpResponseNotFound("Not Found")

        if (
            getattr(request, "organization", None) is None
            and path != "/"
            and not path.startswith(self.ALLOWED_PREFIXES)
            and not is_static_or_media
        ):
            return redirect("home")
        return self.get_response(request)


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
        # Skip non-portal paths (media, static, admin, API, etc.) BEFORE
        # touching request.user: reading the lazy user loads the session
        # from the DB, and exempt file paths must stay DB-free (concurrent
        # photo loads exhausted Postgres connection slots in production).
        for prefix in EXEMPT_PATH_PREFIXES:
            if prefix and request.path.startswith(prefix):
                return self.get_response(request)
        if request.path.startswith("/organizations/support-impersonation/"):
            try:
                if resolve(request.path).view_name in {
                    "organizations:support_impersonation_handoff",
                    "organizations:support_impersonation_redeem",
                    "organizations:support_impersonation_resume",
                    "organizations:support_impersonation_confirm",
                }:
                    return self.get_response(request)
            except Resolver404:
                pass
        if not request.user.is_authenticated:
            return self.get_response(request)
        # Don't redirect away from the agreement page itself.
        if request.path.startswith("/mentor-agreement/"):
            return self.get_response(request)

        # Only apply to users who have a mentor role.
        from programs.permission_views import user_is_mentor

        organization = getattr(request, "organization", None)
        if organization is None or not user_is_mentor(request.user, org=organization):
            return self.get_response(request)

        from programs.models import MentorAgreementAcceptance

        if MentorAgreementAcceptance.has_accepted_for_user(
            request.user, organization=organization
        ):
            return self.get_response(request)

        # User has not accepted — redirect to the agreement page.
        next_url = request.get_full_path()
        return redirect(reverse("mentor_agreement") + f"?next={next_url}")
