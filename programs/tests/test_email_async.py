from unittest import mock

from django.core import mail
from django.core.mail import EmailMessage
from django.core.mail.backends.locmem import EmailBackend as LocmemBackend
from django.test import SimpleTestCase

from programs.utils.email_async import (
    _dispatch_worker,
    send_messages_in_background,
)


class EmailAsyncTests(SimpleTestCase):
    def setUp(self):
        mail.outbox = []

    def test_background_dispatch_completes_in_thread(self):
        backend = LocmemBackend()
        messages = [
            EmailMessage("Subj 1", "Body 1", "from@example.com", ["to1@example.com"]),
            EmailMessage("Subj 2", "Body 2", "from@example.com", ["to2@example.com"]),
        ]

        completed_results = []

        def on_complete(sent_count, err):
            completed_results.append((sent_count, err))

        future = send_messages_in_background(
            messages, connection=backend, on_complete=on_complete, sync=False
        )
        sent = future.result(timeout=5)

        self.assertEqual(sent, 2)
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(len(completed_results), 1)
        self.assertEqual(completed_results[0], (2, None))

    def test_background_dispatch_handles_exceptions_gracefully(self):
        mock_backend = mock.MagicMock()
        mock_backend.send_messages.side_effect = RuntimeError("SMTP connection dropped")

        messages = [
            EmailMessage("Subj", "Body", "from@example.com", ["to@example.com"])
        ]

        completed_results = []

        def on_complete(sent_count, err):
            completed_results.append((sent_count, err))

        future = send_messages_in_background(
            messages, connection=mock_backend, on_complete=on_complete, sync=False
        )
        sent = future.result(timeout=5)

        self.assertEqual(sent, 0)
        self.assertEqual(len(completed_results), 1)
        sent_res, err_res = completed_results[0]
        self.assertEqual(sent_res, 0)
        self.assertIsInstance(err_res, RuntimeError)

    def test_empty_messages_list(self):
        future = send_messages_in_background([])
        sent = future.result(timeout=5)
        self.assertEqual(sent, 0)

    @mock.patch("programs.utils.email_async.close_old_connections")
    def test_db_connections_closed_before_and_after_send(self, mock_close):
        backend = LocmemBackend()
        messages = [
            EmailMessage("Subj", "Body", "from@example.com", ["to@example.com"])
        ]

        _dispatch_worker(messages, connection=backend)
        # Verify close_old_connections was called at least twice (at start and in finally)
        self.assertGreaterEqual(mock_close.call_count, 2)

    def test_callback_exception_does_not_crash_worker(self):
        backend = LocmemBackend()
        messages = [
            EmailMessage("Subj", "Body", "from@example.com", ["to@example.com"])
        ]

        def broken_callback(sent_count, err):
            raise ValueError("Error in callback")

        sent = _dispatch_worker(
            messages, connection=backend, on_complete=broken_callback
        )
        self.assertEqual(sent, 1)
