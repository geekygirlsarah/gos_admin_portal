"""Thread-local access to the currently active request.

Signal handlers (e.g. pre_save receivers that emit AuditLog entries) do not
receive a ``request`` argument. ``AuditContextMiddleware`` stores the active
request here so those handlers can attribute the event to the current actor
(IP, session, and user) via ``audit.service.log_event``.
"""

from __future__ import annotations

import threading

_thread_local = threading.local()


def set_current_request(request):
    """Store the request for the current thread."""
    _thread_local.request = request


def clear_current_request():
    """Drop the stored request so it does not leak across requests."""
    _thread_local.request = None


def get_current_request():
    """Return the active request for this thread, or None outside a request."""
    return getattr(_thread_local, "request", None)
