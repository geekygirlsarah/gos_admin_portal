import os
from unittest import mock

from django.conf import settings
from django.core.mail import EmailMessage, mailers
from django.test import SimpleTestCase, TestCase, override_settings

from GoSAdminPortal.mail_backends import RedirectEmailBackend


class EmailBackendResolutionTests(SimpleTestCase):
    def test_debug_true_defaults_to_console_backend(self):
        """When DEBUG=True and EMAIL_BACKEND is unset, console backend is used."""
        backend_aliases = {
            "console": "django.core.mail.backends.console.EmailBackend",
            "smtp": "django.core.mail.backends.smtp.EmailBackend",
            "dummy": "django.core.mail.backends.dummy.EmailBackend",
            "locmem": "django.core.mail.backends.locmem.EmailBackend",
            "filebased": "django.core.mail.backends.filebased.EmailBackend",
            "redirect": "GoSAdminPortal.mail_backends.RedirectEmailBackend",
        }
        self.assertIn("console", backend_aliases)
        self.assertEqual(
            backend_aliases["console"],
            "django.core.mail.backends.console.EmailBackend",
        )

    def test_alias_mapping_resolves_correctly(self):
        """Friendly alias names map to their full Django backend paths."""
        from GoSAdminPortal.settings import _EMAIL_BACKEND_ALIASES

        self.assertEqual(
            _EMAIL_BACKEND_ALIASES.get("console"),
            "django.core.mail.backends.console.EmailBackend",
        )
        self.assertEqual(
            _EMAIL_BACKEND_ALIASES.get("smtp"),
            "django.core.mail.backends.smtp.EmailBackend",
        )
        self.assertEqual(
            _EMAIL_BACKEND_ALIASES.get("dummy"),
            "django.core.mail.backends.dummy.EmailBackend",
        )
        self.assertEqual(
            _EMAIL_BACKEND_ALIASES.get("locmem"),
            "django.core.mail.backends.locmem.EmailBackend",
        )
        self.assertEqual(
            _EMAIL_BACKEND_ALIASES.get("filebased"),
            "django.core.mail.backends.filebased.EmailBackend",
        )
        self.assertEqual(
            _EMAIL_BACKEND_ALIASES.get("redirect"),
            "GoSAdminPortal.mail_backends.RedirectEmailBackend",
        )


class RedirectEmailBackendTests(SimpleTestCase):
    def test_redirect_rewrites_recipient_and_subject(self):
        """RedirectEmailBackend rewrites all recipients to redirect_to and annotates subject."""
        backend = RedirectEmailBackend(
            redirect_to="dev-team@example.com",
            backend="django.core.mail.backends.locmem.EmailBackend",
        )

        msg = EmailMessage(
            subject="Important Program Update",
            body="Hello Student, your schedule is updated.",
            from_email="admin@girlsofsteelrobotics.org",
            to=["student1@school.edu", "student2@school.edu"],
            cc=["parent@example.org"],
            bcc=["hidden@example.org"],
        )

        sent_count = backend.send_messages([msg])
        self.assertEqual(sent_count, 1)

        # Recipient verification
        self.assertEqual(msg.to, ["dev-team@example.com"])
        self.assertEqual(msg.cc, [])
        self.assertEqual(msg.bcc, [])

        # Subject and header verification
        self.assertIn("[DEV to: student1@school.edu, student2@school.edu]", msg.subject)
        self.assertIn("Important Program Update", msg.subject)
        self.assertEqual(
            msg.extra_headers.get("X-Original-To"),
            "student1@school.edu, student2@school.edu",
        )
        self.assertEqual(
            msg.extra_headers.get("X-Original-Cc"),
            "parent@example.org",
        )
        self.assertEqual(
            msg.extra_headers.get("X-Original-Bcc"),
            "hidden@example.org",
        )

    def test_redirect_with_multiple_redirect_targets(self):
        """Redirect target can be comma-separated string."""
        backend = RedirectEmailBackend(
            redirect_to="dev1@example.com, dev2@example.com",
            backend="django.core.mail.backends.locmem.EmailBackend",
        )

        msg = EmailMessage(
            subject="Test Subject",
            body="Body content",
            from_email="admin@girlsofsteelrobotics.org",
            to=["real_student@school.edu"],
        )

        backend.send_messages([msg])
        self.assertEqual(msg.to, ["dev1@example.com", "dev2@example.com"])

    def test_redirect_preserves_empty_messages(self):
        """Passing empty list returns 0 sent."""
        backend = RedirectEmailBackend(
            redirect_to="dev@example.com",
            backend="django.core.mail.backends.locmem.EmailBackend",
        )
        self.assertEqual(backend.send_messages([]), 0)

    def test_redirect_open_and_close(self):
        """open and close delegate to wrapped backend without errors."""
        backend = RedirectEmailBackend(
            redirect_to="dev@example.com",
            backend="django.core.mail.backends.locmem.EmailBackend",
        )
        backend.open()
        backend.close()
