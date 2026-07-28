from django.http import Http404

from .models import KioskConfig

_COOKIE_MAX_AGE = 7 * 24 * 60 * 60  # 7 days in seconds
_CODE_EXPIRY = 600  # 10 minutes


def _cookie_name(kiosk_id):
    return f"kiosk_unlocked_{kiosk_id}"


def _get_kiosk_or_404(kiosk_id):
    try:
        return KioskConfig.objects.select_related("program").get(
            pk=kiosk_id, is_active=True
        )
    except KioskConfig.DoesNotExist:
        raise Http404("Kiosk not found or inactive.")


def _is_unlocked(request, kiosk_id):
    return request.COOKIES.get(_cookie_name(kiosk_id)) == "1"
