"""Email backends for GoSAdminPortal."""

from __future__ import annotations

import logging
from typing import Any, Sequence

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend
from django.core.mail.message import EmailMessage
from django.utils.module_loading import import_string

logger = logging.getLogger(__name__)


class RedirectEmailBackend(BaseEmailBackend):
    """Email backend that intercepts all outgoing messages and reroutes them to a safe
    redirect address (e.g. ``dev-team@example.com``), preventing any email from reaching
    real recipients during local development or staging testing.

    The original recipients (To, CC, BCC) are annotated in the subject line and preserved
    in `X-Original-To`, `X-Original-Cc`, and `X-Original-Bcc` email headers.
    """

    def __init__(
        self,
        redirect_to: str | Sequence[str] | None = None,
        backend: str | type[BaseEmailBackend] | None = None,
        backend_options: dict[str, Any] | None = None,
        fail_silently: bool = False,
        alias: str | None = None,
        **kwargs: Any,
    ):
        super().__init__(alias=alias)
        self.fail_silently = fail_silently
        self.redirect_to = redirect_to or getattr(settings, "EMAIL_REDIRECT_TO", None)
        self._target_emails: list[str] = []
        if isinstance(self.redirect_to, str):
            self._target_emails = [
                e.strip() for e in self.redirect_to.split(",") if e.strip()
            ]
        elif isinstance(self.redirect_to, (list, tuple)):
            self._target_emails = [
                str(e).strip() for e in self.redirect_to if str(e).strip()
            ]

        # Initialize underlying backend
        if backend is None:
            backend_cls = import_string("django.core.mail.backends.smtp.EmailBackend")
        elif isinstance(backend, str):
            backend_cls = import_string(backend)
        else:
            backend_cls = backend

        opts = dict(backend_options or {})
        self.wrapped_backend: BaseEmailBackend = backend_cls(**opts)

    def open(self):
        return self.wrapped_backend.open()

    def close(self):
        return self.wrapped_backend.close()

    def send_messages(self, email_messages: Sequence[EmailMessage]) -> int:
        if not email_messages:
            return 0

        target_recipients = self._target_emails
        if not target_recipients:
            logger.warning(
                "RedirectEmailBackend active but EMAIL_REDIRECT_TO is not configured. Messages will not be delivered."
            )
            return len(email_messages)

        modified_messages = []
        for msg in email_messages:
            orig_to = list(msg.to or [])
            orig_cc = list(msg.cc or [])
            orig_bcc = list(msg.bcc or [])

            # Prepare header metadata
            msg.extra_headers = dict(msg.extra_headers or {})
            if orig_to:
                msg.extra_headers["X-Original-To"] = ", ".join(orig_to)
            if orig_cc:
                msg.extra_headers["X-Original-Cc"] = ", ".join(orig_cc)
            if orig_bcc:
                msg.extra_headers["X-Original-Bcc"] = ", ".join(orig_bcc)

            # Override recipient lists
            msg.to = list(target_recipients)
            msg.cc = []
            msg.bcc = []

            # Annotate subject
            orig_display = ", ".join(orig_to) if orig_to else "unknown"
            msg.subject = f"[DEV to: {orig_display}] {msg.subject}"

            modified_messages.append(msg)

        return self.wrapped_backend.send_messages(modified_messages)
