from django.contrib import messages
from django.shortcuts import redirect, render


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
