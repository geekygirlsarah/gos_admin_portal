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
                    att_name = str(filename or "attachment")
                    mimetype_str = str(mimetype or "application/octet-stream")
                    attachments_payload.append(
                        {
                            "name": att_name,
                            "filename": att_name,
                            "content": b64_content,
                            "type": mimetype_str,
                            "content_type": mimetype_str,
                        }
                    )
                elif isinstance(att, email.message.Message):
                    # MIME message format (e.g. MIMEImage, MIMEBase, MIMEPart)
                    filename = att.get_filename() or "attachment"
                    mimetype_str = str(
                        att.get_content_type() or "application/octet-stream"
                    )
                    payload_bytes = att.get_payload(decode=True)
                    if payload_bytes is None:
                        raw = att.get_payload()
                        payload_bytes = (
                            raw.encode("utf-8") if isinstance(raw, str) else bytes(raw)
                        )
                    b64_content = base64.b64encode(payload_bytes).decode("ascii")
                    att_name = str(filename or "attachment")
                    att_dict: dict[str, Any] = {
                        "name": att_name,
                        "filename": att_name,
                        "content": b64_content,
                        "type": mimetype_str,
                        "content_type": mimetype_str,
                    }
                    if "Content-ID" in att:
                        att_dict["content_id"] = att["Content-ID"]
                    disposition = att.get_content_disposition()
                    if disposition:
                        att_dict["disposition"] = disposition
                    attachments_payload.append(att_dict)

        if attachments_payload:
            payload["attachments"] = attachments_payload

        return payload

    def _message_recipient_addresses(self, msg: EmailMessage) -> list[str]:
        addresses = []
        for recipient in (msg.to or []) + (msg.cc or []) + (msg.bcc or []):
            address = _parse_email_address(recipient).get("email", "").strip()
            if address:
                addresses.append(address)
        return addresses

    def _record_successful_message(self, msg, provider_message_id):
        if not getattr(msg, "_momentum_delivery_tracking", False):
            return
        organization_id = getattr(msg, "_momentum_organization_id", None)
        recipients = self._message_recipient_addresses(msg)
        if not organization_id or not provider_message_id or len(recipients) != 1:
            return
        try:
            from programs.utils.tosend_webhooks import record_trusted_outgoing_message

            record_trusted_outgoing_message(
                organization_id, provider_message_id, recipients
            )
        except Exception as exc:
            logger.warning(
                "ToSend accepted an email but its tenant mapping was not recorded (%s).",
                type(exc).__name__,
            )

    @staticmethod
    def _batch_results(response):
        try:
            result = response.json()
        except (TypeError, ValueError):
            return None
        results = result.get("results") if isinstance(result, dict) else None
        return results if isinstance(results, list) else None

    def _record_batch_successes(self, chunk, results):
        if results is None or len(results) != len(chunk):
            return
        message_ids = [
            (
                result.get("message_id")
                if isinstance(result, dict) and result.get("status") == "success"
                else None
            )
            for result in results
        ]
        id_counts = {}
        for message_id in message_ids:
            if isinstance(message_id, str) and message_id:
                id_counts[message_id] = id_counts.get(message_id, 0) + 1
        for msg, result, message_id in zip(chunk, results, message_ids):
            if (
                isinstance(result, dict)
                and result.get("status") == "success"
                and isinstance(message_id, str)
                and message_id
                and id_counts[message_id] == 1
            ):
                self._record_successful_message(msg, message_id)

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
                if response.status_code == 200:
                    sent_count += 1
                    try:
                        result = response.json()
                    except (TypeError, ValueError):
                        result = None
                    message_id = (
                        result.get("message_id") if isinstance(result, dict) else None
                    )
                    if isinstance(message_id, str) and message_id:
                        self._record_successful_message(msg, message_id)
                else:
                    self._handle_error_response(response)
            except Exception as exc:
                logger.error("ToSendEmailBackend send failed (%s).", type(exc).__name__)
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
                    if response.status_code == 200:
                        sent_count += len(chunk)
                        if len(chunk) == 1:
                            self._record_batch_successes(
                                chunk, self._batch_results(response)
                            )
                    elif response.status_code == 207:
                        results = self._batch_results(response)
                        if results is not None:
                            sent_count += sum(
                                isinstance(result, dict)
                                and result.get("status") == "success"
                                for result in results
                            )
                        self._record_batch_successes(chunk, results)
                    else:
                        self._handle_error_response(response)
                except Exception as exc:
                    logger.error(
                        "ToSendEmailBackend batch send failed (%s).",
                        type(exc).__name__,
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
        logger.error(
            "ToSendEmailBackend error: HTTP %s | Retry-After: %s",
            status,
            retry_after,
        )
        if not self.fail_silently:
            if status >= 400:
                response.raise_for_status()
            raise requests.HTTPError(
                f"Unexpected toSend response HTTP {status}", response=response
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
            msg._momentum_delivery_tracking = False
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
