import logging

from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie

from .models import KioskConfig
from .kiosk_utils import _get_kiosk_or_404, _is_unlocked

logger = logging.getLogger(__name__)


def kiosk_index(request):
    """Landing page for kiosks.
    Lists all active kiosks.
    """
    kiosks = KioskConfig.objects.filter(is_active=True).select_related("program")
    # Also filter by programs that have attendance feature enabled
    kiosks = kiosks.filter(program__features__key="attendance").distinct()
    
    return render(
        request,
        "kiosk/index.html",
        {
            "kiosks": kiosks,
        },
    )


@ensure_csrf_cookie
def kiosk_signin(request, kiosk_id):
    """Public kiosk sign-in page.

    Renders a full-screen attendance kiosk page.  If the kiosk has been
    unlocked by a mentor (HttpOnly cookie present), the normal sign-in UI is
    shown.  Otherwise an unlock form is displayed.
    """
    config = _get_kiosk_or_404(kiosk_id)
    is_unlocked = _is_unlocked(request, kiosk_id)
    return render(
        request,
        "kiosk/signin.html",
        {
            "kiosk": config,
            "program": config.program,
            "program_id": config.program_id,
            "is_unlocked": is_unlocked,
        },
    )




