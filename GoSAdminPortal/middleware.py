import logging

from asgiref.sync import iscoroutinefunction, sync_to_async
from django.conf import settings
from django.shortcuts import redirect
from django.urls import Resolver404, resolve

logger = logging.getLogger(__name__)

EXEMPT_URL_NAMES = {
    "account_login",
    "account_logout",
    "account_signup",
    "account_confirm_email",
    "home",  # home requires login via decorator, but keep to avoid loops
    "admin:login",
}

EXEMPT_PATH_PREFIXES = (
    "/accounts/",
    "/admin/",
    "/apply/",  # public application wizard
    settings.MEDIA_URL,  # uploaded files (e.g., blank program documents linked from /apply/)
    settings.STATIC_URL,
)


class LoginRequiredMiddleware:
    """Redirect anonymous users to login for all pages except exempt ones."""

    sync_capable = True
    async_capable = True

    def __init__(self, get_response):
        self.get_response = get_response
        self._is_async = iscoroutinefunction(get_response)

    def __call__(self, request):
        if self._is_async:
            return self.__acall__(request)

        if request.user.is_authenticated:
            return self.get_response(request)

        if self._is_exempt(request.path):
            return self.get_response(request)

        return redirect(settings.LOGIN_URL + f"?next={request.get_full_path()}")

    async def __acall__(self, request):
        if request.user.is_authenticated:
            return await self.get_response(request)

        # In async context, resolve might be sync.
        if await sync_to_async(self._is_exempt)(request.path):
            return await self.get_response(request)

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
            pass
        except Exception:
            logger.debug("Unexpected error resolving path %s", path, exc_info=True)
        return False
