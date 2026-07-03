import pghistory.middleware
from django.db import connection


class AuditHistoryMiddleware(pghistory.middleware.HistoryMiddleware):
    """
    Extends pghistory's built-in HistoryMiddleware to attach the current
    user ID, client IP, and session key to every pghistory Context created
    during a request.  This metadata is stored alongside every
    trigger-captured model change event automatically.
    """

    def __call__(self, request):
        if connection.vendor != "postgresql":
            return self.get_response(request)
        return super().__call__(request)

    def get_context(self, request) -> dict:
        context = super().get_context(request)

        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            context["user_id"] = user.pk

        x_forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
        if x_forwarded:
            context["ip_address"] = x_forwarded.split(",")[0].strip()
        else:
            context["ip_address"] = request.META.get("REMOTE_ADDR", "")

        try:
            context["session_id"] = request.session.session_key or ""
        except AttributeError:
            context["session_id"] = ""

        return context
