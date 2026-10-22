import base64
from unittest import mock

from django.core.mail import EmailMessage, EmailMultiAlternatives
from django.test import TestCase

from GoSAdminPortal.mail_backends import RedirectEmailBackend, ToSendEmailBackend


class ToSendEmailBackendTests(TestCase):
    def setUp(self):
        self.backend = ToSendEmailBackend(
            api_key="ts_test_key_12345",
            api_url="https://api.tosend.com",
            fail_silently=False,
        )

    @mock.patch("requests.post")
    def test_single_email_payload_serialization(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {
            "id": "email_123",
            "status": "queued",
        }

        msg = EmailMessage(
            subject="Test Subject",
            body="Hello, world!",
            from_email="Girls of Steel <leads@girlsofsteelrobotics.org>",
            to=["Student <student@example.com>"],
            cc=["mentor@example.com"],
            bcc=["archive@example.com"],
            reply_to=["reply@example.com"],
            headers={"X-Custom-Header": "CustomValue"},
        )

        sent = self.backend.send_messages([msg])
        self.assertEqual(sent, 1)

        self.assertEqual(mock_post.call_count, 1)
        url, kwargs = mock_post.call_args
        self.assertEqual(url[0], "https://api.tosend.com/v2/emails")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer ts_test_key_12345")
        self.assertEqual(kwargs["headers"]["Content-Type"], "application/json")

        payload = kwargs["json"]
        self.assertEqual(
            payload["from"],
            {"email": "leads@girlsofsteelrobotics.org", "name": "Girls of Steel"},
        )
        self.assertEqual(
            payload["to"], [{"email": "student@example.com", "name": "Student"}]
        )
        self.assertEqual(payload["cc"], [{"email": "mentor@example.com"}])
        self.assertEqual(payload["bcc"], [{"email": "archive@example.com"}])
        self.assertEqual(payload["reply_to"], [{"email": "reply@example.com"}])
        self.assertEqual(payload["subject"], "Test Subject")
        self.assertEqual(payload["text"], "Hello, world!")
        self.assertEqual(payload["headers"], {"X-Custom-Header": "CustomValue"})

    @mock.patch("requests.post")
    def test_attachments_and_html_alternatives(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"id": "email_456"}

        msg = EmailMultiAlternatives(
            subject="HTML & Attachments",
            body="Plain text body",
            from_email="admin@example.com",
            to=["recipient@example.com"],
        )
        msg.attach_alternative("<h1>HTML Body</h1>", "text/html")

        # Tuple attachment
        raw_pdf = b"%PDF-1.4 sample content"
        msg.attach("document.pdf", raw_pdf, "application/pdf")

        # Image attachment as tuple
        png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00"
        msg.attach("logo.png", png_bytes, "image/png")

        sent = self.backend.send_messages([msg])
        self.assertEqual(sent, 1)

        payload = mock_post.call_args[1]["json"]
        self.assertEqual(payload["text"], "Plain text body")
        self.assertEqual(payload["html"], "<h1>HTML Body</h1>")

        attachments = payload["attachments"]
        self.assertEqual(len(attachments), 2)

        pdf_att = attachments[0]
        self.assertEqual(pdf_att["filename"], "document.pdf")
        self.assertEqual(pdf_att["content_type"], "application/pdf")
        self.assertEqual(
            base64.b64decode(pdf_att["content"]).decode("latin1"),
            raw_pdf.decode("latin1"),
        )

        img_att = attachments[1]
        self.assertEqual(img_att["filename"], "logo.png")
        self.assertEqual(img_att["content_type"], "image/png")
        self.assertEqual(
            base64.b64decode(img_att["content"]),
            png_bytes,
        )

    @mock.patch("requests.post")
    def test_batch_emails_chunking_over_100(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"status": "success"}

        # Create 125 distinct messages
        messages = [
            EmailMessage(
                subject=f"Bulk Announcement #{i}",
                body=f"Body {i}",
                from_email="leads@girlsofsteelrobotics.org",
                to=[f"student{i}@example.com"],
            )
            for i in range(125)
        ]

        sent = self.backend.send_messages(messages)
        self.assertEqual(sent, 125)

        self.assertEqual(mock_post.call_count, 2)
        call_1_url, call_1_kwargs = mock_post.call_args_list[0]
        call_2_url, call_2_kwargs = mock_post.call_args_list[1]

        self.assertEqual(call_1_url[0], "https://api.tosend.com/v2/emails/batch")
        self.assertEqual(call_2_url[0], "https://api.tosend.com/v2/emails/batch")

        self.assertEqual(len(call_1_kwargs["json"]), 100)
        self.assertEqual(len(call_2_kwargs["json"]), 25)

    @mock.patch("requests.post")
    def test_api_error_handling_and_fail_silently(self, mock_post):
        # 401 Unauthorized with fail_silently=False
        mock_post.return_value.status_code = 401
        mock_post.return_value.text = '{"error": "Unauthorized"}'

        msg = EmailMessage("Subj", "Body", "from@example.com", ["to@example.com"])

        with self.assertRaises(Exception):
            self.backend.send_messages([msg])

        # With fail_silently=True
        silent_backend = ToSendEmailBackend(api_key="ts_test_key", fail_silently=True)
        sent = silent_backend.send_messages([msg])
        self.assertEqual(sent, 0)

    @mock.patch("requests.post")
    def test_multi_status_207_batch_response(self, mock_post):
        # 207 Multi-Status partial batch success
        mock_post.return_value.status_code = 207
        mock_post.return_value.json.return_value = {
            "results": [
                {"id": "1", "status": "success"},
                {"id": "2", "status": "error", "error": "Invalid email"},
            ]
        }

        messages = [
            EmailMessage("Subj 1", "Body 1", "from@example.com", ["to1@example.com"]),
            EmailMessage("Subj 2", "Body 2", "from@example.com", ["to2@example.com"]),
        ]

        sent = self.backend.send_messages(messages)
        self.assertEqual(sent, 1)

    def test_missing_api_key_raises_or_fails_silently(self):
        backend_no_key = ToSendEmailBackend(api_key="", fail_silently=False)
        msg = EmailMessage("Subj", "Body", "from@example.com", ["to@example.com"])

        with self.assertRaises(ValueError):
            backend_no_key.send_messages([msg])

        silent_no_key = ToSendEmailBackend(api_key="", fail_silently=True)
        self.assertEqual(silent_no_key.send_messages([msg]), 0)

    @mock.patch("requests.post")
    def test_redirect_backend_with_tosend(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"id": "email_redir"}

        redirect_backend = RedirectEmailBackend(
            redirect_to="dev-team@example.com",
            backend="GoSAdminPortal.mail_backends.ToSendEmailBackend",
            backend_options={"api_key": "ts_redirect_key"},
        )

        msg = EmailMessage(
            subject="Real Notification",
            body="Important Info",
            from_email="leads@girlsofsteelrobotics.org",
            to=["student@example.com"],
            cc=["parent@example.com"],
        )

        sent = redirect_backend.send_messages([msg])
        self.assertEqual(sent, 1)

        payload = mock_post.call_args[1]["json"]
        self.assertEqual(payload["to"], [{"email": "dev-team@example.com"}])
        self.assertNotIn("cc", payload)
        self.assertEqual(
            payload["subject"], "[DEV to: student@example.com] Real Notification"
        )
        self.assertEqual(payload["headers"]["X-Original-To"], "student@example.com")
        self.assertEqual(payload["headers"]["X-Original-Cc"], "parent@example.com")
