"""Email backends for GoSAdminPortal."""

from __future__ import annotations

import base64
import email.message
import email.utils
import logging
from typing import Any, Sequence

import requests
from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend
from django.core.mail.message import EmailMessage, EmailMultiAlternatives
from django.utils.module_loading import import_string

logger = logging.getLogger(__name__)


def _parse_email_address(addr: str) -> dict[str, str]:
    """Parse an email address into a dict with 'email' and optional 'name'."""
    name, email_addr = email.utils.parseaddr(str(addr))
    email_addr = email_addr.strip()
    if not email_addr:
        email_addr = str(addr).strip()
    if name:
        return {"email": email_addr, "name": name}
    return {"email": email_addr}


class ToSendEmailBackend(BaseEmailBackend):
    """Email backend for delivering emails via the toSend v2 REST API (https://api.tosend.com/v2/).

    Supports both single email delivery (POST /v2/emails) and batch delivery
    (POST /v2/emails/batch) chunked into batches of up to 100 messages.
    """

    BATCH_SIZE = 100

    def __init__(
        self,
        api_key: str | None = None,
        api_url: str | None = None,
        timeout: int | None = None,
        fail_silently: bool = False,
        alias: str | None = None,
        **kwargs: Any,
    ):
        super().__init__(alias=alias)
        self.fail_silently = fail_silently
        self.api_key = (
            api_key if api_key is not None else getattr(settings, "TOSEND_API_KEY", "")
        )
        self.api_url = (
            api_url
            if api_url is not None
            else getattr(settings, "TOSEND_API_URL", "https://api.tosend.com")
        ).rstrip("/")
        self.timeout = (
            timeout if timeout is not None else getattr(settings, "TOSEND_TIMEOUT", 30)
        )

    def _serialize_message(self, msg: EmailMessage) -> dict[str, Any]:
        """Serialize a Django EmailMessage or EmailMultiAlternatives into a toSend API payload dict."""
        from_dict = _parse_email_address(
            msg.from_email or getattr(settings, "DEFAULT_FROM_EMAIL", "")
        )

        payload: dict[str, Any] = {
            "from": from_dict,
            "to": [_parse_email_address(addr) for addr in (msg.to or [])],
            "subject": msg.subject or "",
            "text": msg.body or "",
        }

        if msg.cc:
            payload["cc"] = [_parse_email_address(addr) for addr in msg.cc]
        if msg.bcc:
            payload["bcc"] = [_parse_email_address(addr) for addr in msg.bcc]
        if msg.reply_to:
            payload["reply_to"] = [_parse_email_address(addr) for addr in msg.reply_to]

        # Extract HTML body from alternatives or content_subtype
        html_body = None
        if isinstance(msg, EmailMultiAlternatives) and msg.alternatives:
            for content, mimetype in msg.alternatives:
                if mimetype == "text/html":
                    html_body = content
                    break
        elif getattr(msg, "content_subtype", None) == "html" and isinstance(
            msg.body, str
        ):
            html_body = msg.body

        if html_body:
            payload["html"] = html_body

        if msg.extra_headers:
            payload["headers"] = dict(msg.extra_headers)

        attachments_payload = []
        if getattr(msg, "attachments", None):
            for att in msg.attachments:
                if isinstance(att, tuple):
                    # Tuple format: (filename, content, mimetype)
                    filename, content, mimetype = att
                    if isinstance(content, str):
                        content_bytes = content.encode("utf-8")
                    else:
                        content_bytes = bytes(content)
                    b64_content = base64.b64encode(content_bytes).decode("ascii")
                    attachments_payload.append(
                        {
                            "filename": filename or "attachment",
                            "content": b64_content,
                            "content_type": mimetype or "application/octet-stream",
                        }
                    )
                elif isinstance(att, email.message.Message):
                    # MIME message format (e.g. MIMEImage, MIMEBase)
                    filename = att.get_filename() or "attachment"
                    mimetype = att.get_content_type()
                    payload_bytes = att.get_payload(decode=True)
                    if payload_bytes is None:
                        raw = att.get_payload()
                        payload_bytes = (
                            raw.encode("utf-8") if isinstance(raw, str) else bytes(raw)
                        )
                    b64_content = base64.b64encode(payload_bytes).decode("ascii")
                    att_dict: dict[str, Any] = {
                        "filename": filename,
                        "content": b64_content,
                        "content_type": mimetype,
                    }
                    if "Content-ID" in att:
                        att_dict["content_id"] = att["Content-ID"]
                    attachments_payload.append(att_dict)

        if attachments_payload:
            payload["attachments"] = attachments_payload

        return payload

    def send_messages(self, email_messages: Sequence[EmailMessage]) -> int:
        if not email_messages:
            return 0

        if not self.api_key:
            err_msg = "ToSendEmailBackend: TOSEND_API_KEY is not configured."
            logger.error(err_msg)
            if not self.fail_silently:
                raise ValueError(err_msg)
            return 0

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "GoSAdminPortal-ToSendBackend/1.0",
        }

        sent_count = 0
        valid_messages = [msg for msg in email_messages if msg]
        if not valid_messages:
            return 0

        if len(valid_messages) == 1:
            # Single email endpoint: POST /v2/emails
            msg = valid_messages[0]
            payload = self._serialize_message(msg)
            url = f"{self.api_url}/v2/emails"
            try:
                response = requests.post(
                    url, json=payload, headers=headers, timeout=self.timeout
                )
                if response.status_code in (200, 201, 202):
                    sent_count += 1
                else:
                    self._handle_error_response(response)
            except Exception as e:
                logger.error("ToSendEmailBackend send failed: %s", e, exc_info=True)
                if not self.fail_silently:
                    raise
        else:
            # Batch endpoint: POST /v2/emails/batch in chunks of up to 100
            for i in range(0, len(valid_messages), self.BATCH_SIZE):
                chunk = valid_messages[i : i + self.BATCH_SIZE]
                payloads = [self._serialize_message(msg) for msg in chunk]
                url = f"{self.api_url}/v2/emails/batch"
                try:
                    response = requests.post(
                        url, json=payloads, headers=headers, timeout=self.timeout
                    )
                    if response.status_code in (200, 201, 202):
                        sent_count += len(chunk)
                    elif response.status_code == 207:
                        # Multi-Status: partial batch success
                        try:
                            data = response.json()
                            results = data.get("results") or data.get("data") or []
                            chunk_sent = 0
                            for res in results:
                                if isinstance(res, dict) and res.get("status") in (
                                    "success",
                                    "queued",
                                    "sent",
                                ):
                                    chunk_sent += 1
                                elif (
                                    isinstance(res, dict)
                                    and res.get("id")
                                    and not res.get("error")
                                ):
                                    chunk_sent += 1
                            sent_count += chunk_sent
                        except Exception:
                            sent_count += len(chunk)
                    else:
                        self._handle_error_response(response)
                except Exception as e:
                    logger.error(
                        "ToSendEmailBackend batch send failed: %s", e, exc_info=True
                    )
                    if not self.fail_silently:
                        raise

        return sent_count

    def _handle_error_response(self, response: requests.Response) -> None:
        status = response.status_code
        retry_after = (
            response.headers.get("Retry-After")
            if hasattr(response.headers, "get")
            else None
        )
        err_detail = getattr(response, "text", "")
        logger.error(
            "ToSendEmailBackend error: HTTP %s | Retry-After: %s | Response: %s",
            status,
            retry_after,
            err_detail,
        )
        if not self.fail_silently:
            try:
                response.raise_for_status()
            except Exception:
                raise
            if status >= 400:
                raise requests.HTTPError(
                    f"HTTP {status}: {err_detail}", response=response
                )


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
