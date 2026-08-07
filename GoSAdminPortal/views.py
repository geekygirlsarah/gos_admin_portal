from django.contrib import messages
from django.core import mail
from django.db import connection
from django.http import JsonResponse
from django.shortcuts import redirect, render


def health(request):
    """Health check endpoint for Render.

    Verifies the database connection and outgoing email backend are alive:
      - 200 {"status": "ok", "db": "ok", "email": "ok"} when healthy
      - 503 {"status": "unhealthy", ...} with per-component details on failure
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

    try:
        conn = mail.get_connection()
        conn.open()
        conn.close()
    except Exception:
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
