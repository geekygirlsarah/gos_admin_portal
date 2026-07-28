import logging
import zoneinfo

from django.conf import settings
from django.shortcuts import redirect
from django.urls import Resolver404, resolve
from django.utils import timezone
from django.utils.deprecation import MiddlewareMixin

logger = logging.getLogger(__name__)

EXEMPT_URL_NAMES = {
    "account_login",
    "account_logout",
    "account_signup",
    "account_confirm_email",
    "admin:login",
    "privacy_policy",
    "non_discrimination_policy",
}

EXEMPT_PATH_PREFIXES = (
    "/accounts/",
    "/admin/",
    "/apply/",  # public application wizard
    "/api/v1/",  # API endpoints use X-API-KEY header auth, not session auth
    "/kiosk/",  # public kiosk attendance sign-in pages
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

        # Allow named urls in exempt set
        try:
            match = resolve(path)
            if match.view_name in EXEMPT_URL_NAMES:
                return True
        except Resolver404:
            # The path doesn't resolve to any known URL. Do NOT treat this as
            # exempt: an anonymous user hitting an unknown path should still
            # be redirected to login rather than being shown a bare 404,
            # which would otherwise leak information about which paths exist.
            return False
        except Exception:
            logger.debug("Unexpected error resolving path %s", path, exc_info=True)
        return False


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
