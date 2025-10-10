from functools import wraps
from django.http import JsonResponse
from django.utils import timezone

from .models import ApiClientKey


def require_api_key(scope_required: str = 'read'):
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            key = request.headers.get('X-API-KEY') or request.META.get('HTTP_X_API_KEY')
            if not key:
                return JsonResponse({'error': 'Missing API key'}, status=401)
            try:
                client = ApiClientKey.objects.get(key=key, is_active=True)
            except ApiClientKey.DoesNotExist:
                return JsonResponse({'error': 'Invalid API key'}, status=401)

            # Check scope
            if scope_required == 'write' and client.scope != ApiClientKey.SCOPE_WRITE:
                return JsonResponse({'error': 'Insufficient scope: write required'}, status=403)

            # Attach client to request for logging if needed
            request.api_client = client
            client.last_used_at = timezone.now()
            client.save(update_fields=['last_used_at'])
            return view_func(request, *args, **kwargs)
        return _wrapped
    return decorator
