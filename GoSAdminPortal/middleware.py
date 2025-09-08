from django.conf import settings
from django.shortcuts import redirect
from django.urls import resolve

EXEMPT_URL_NAMES = {
    'account_login',
    'account_logout',
    'account_signup',
    'account_confirm_email',
    'home',  # home requires login via decorator, but keep to avoid loops
    'admin:login',
}

EXEMPT_PATH_PREFIXES = (
    '/accounts/',
    '/admin/',
    settings.STATIC_URL,
)

class LoginRequiredMiddleware:
    """Redirect anonymous users to login for all pages except exempt ones."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            return self.get_response(request)

        path = request.path
        # Allow exempt prefixes
        for prefix in EXEMPT_PATH_PREFIXES:
            if prefix and path.startswith(prefix):
                return self.get_response(request)

        # Allow named urls in exempt set
        try:
            match = resolve(path)
            if match.view_name in EXEMPT_URL_NAMES:
                return self.get_response(request)
        except Exception:
            pass

        return redirect(settings.LOGIN_URL + f'?next={request.get_full_path()}')
