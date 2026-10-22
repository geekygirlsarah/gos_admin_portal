"""Asynchronous in-process email dispatch utilities."""

from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable, Sequence

from django.conf import settings
from django.core.mail import get_connection
from django.core.mail.backends.base import BaseEmailBackend
from django.core.mail.message import EmailMessage
from django.db import close_old_connections

logger = logging.getLogger(__name__)

# Shared bounded thread pool executor for background email delivery
_EMAIL_EXECUTOR: ThreadPoolExecutor | None = None


def get_email_executor(max_workers: int = 4) -> ThreadPoolExecutor:
    """Return or initialize the singleton ThreadPoolExecutor for email sending."""
    global _EMAIL_EXECUTOR
    if _EMAIL_EXECUTOR is None:
        _EMAIL_EXECUTOR = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="email_dispatcher",
        )
    return _EMAIL_EXECUTOR


def _dispatch_worker(
    email_messages: Sequence[EmailMessage],
    connection: BaseEmailBackend | None = None,
    on_complete: Callable[[int, Exception | None], None] | None = None,
) -> int:
    """Worker function executed inside the background thread.

    Manages Django database connections before and after sending, logs results,
    and invokes the optional completion callback.
    """
    sent_count = 0
    err: Exception | None = None
    try:
        close_old_connections()
        backend = connection or get_connection()
        sent_count = backend.send_messages(email_messages)
        logger.info(
            "Background email dispatch succeeded | message_count=%d | sent_count=%d",
            len(email_messages),
            sent_count,
        )
    except Exception as e:
        err = e
        logger.error(
            "Background email dispatch failed | message_count=%d | error=%s",
            len(email_messages),
            e,
            exc_info=True,
        )
    finally:
        close_old_connections()
        if on_complete:
            try:
                on_complete(sent_count, err)
            except Exception as cb_err:
                logger.error(
                    "Background email dispatch on_complete callback failed: %s",
                    cb_err,
                    exc_info=True,
                )

    return sent_count


def send_messages_in_background(
    email_messages: Sequence[EmailMessage],
    connection: BaseEmailBackend | None = None,
    on_complete: Callable[[int, Exception | None], None] | None = None,
    executor: ThreadPoolExecutor | None = None,
    sync: bool | None = None,
) -> Future[int]:
    """Submit sending of email messages to the background thread pool.

    In test environments (TESTING=True) or when sync=True is explicitly passed,
    executes immediately so test assertions on mail.outbox/database are deterministic.
    Returns a concurrent.futures.Future representing the execution.
    """
    is_sync = sync if sync is not None else getattr(settings, "TESTING", False)
    if is_sync:
        sent = _dispatch_worker(email_messages, connection, on_complete)
        future: Future[int] = Future()
        future.set_result(sent)
        return future

    pool = executor or get_email_executor()
    if not email_messages:
        return pool.submit(lambda: 0)

    return pool.submit(_dispatch_worker, email_messages, connection, on_complete)
