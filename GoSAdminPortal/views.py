from django.conf import settings
from django.contrib import messages
from django.core import mail
from django.core.cache import cache
from django.db import connection
from django.http import JsonResponse
from django.shortcuts import redirect, render

EMAIL_HEALTH_CACHE_KEY = "health:email_status"


def health(request):
    """Health check endpoint for Render.

    Verifies the database connection and outgoing email backend are alive:
      - 200 {"status": "ok", "db": "ok", "email": "ok"} when healthy
      - 503 {"status": "unhealthy", ...} with per-component details on failure

    Render probes this endpoint every few seconds, so the SMTP check result is
    cached for HEALTH_SMTP_CHECK_INTERVAL seconds to avoid opening a connection
    to the mail server on every single probe. Failures use a shorter cooldown
    (HEALTH_SMTP_FAILURE_COOLDOWN) so a recovery is detected promptly.
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        return JsonResponse(
            {"status": "unhealthy", "db": "unavailable"},
            status=503,
        )

    email_status = cache.get(EMAIL_HEALTH_CACHE_KEY)
    if email_status is None:
        try:
            conn = mail.mailers.default
            conn.open()
            conn.close()
            email_status = "ok"
            ttl = settings.HEALTH_SMTP_CHECK_INTERVAL
        except Exception:
            email_status = "unavailable"
            ttl = settings.HEALTH_SMTP_FAILURE_COOLDOWN
        cache.set(EMAIL_HEALTH_CACHE_KEY, email_status, ttl)

    if email_status != "ok":
        return JsonResponse(
            {"status": "unhealthy", "db": "ok", "email": "unavailable"},
            status=503,
        )

    return JsonResponse({"status": "ok", "db": "ok", "email": "ok"})


def handler404(request, exception=None):
    """
    Custom 404 handler that redirects to relevant pages with a message.
    """
    if request.path.startswith("/apply/"):
        messages.error(request, "That application doesn't exist or timed out")
        return redirect("apply_start")

    messages.error(request, "That page doesn't exist")
    return redirect("home")


def handler403(request, exception=None):
    """
    Custom 403 handler that shows a friendly error page.
    """
    return render(request, "403.html", status=403)


def handler400(request, exception=None):
    """
    Custom 400 handler that shows a friendly error page.
    """
    return render(request, "400.html", status=400)


def handler500(request):
    """
    Custom 500 handler that shows a friendly error page.
    """
    return render(request, "500.html", status=500)
