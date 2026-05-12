"""Tests for the Application and SiteSettings models."""
from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from applications.models import (
    APP_ID_ALPHABET,
    APP_ID_LENGTH,
    Application,
    SiteSettings,
    generate_application_id,
    generate_otp_code,
)


class ApplicationIdGenerationTests(TestCase):
    def test_generated_id_uses_correct_alphabet_and_length(self):
        for _ in range(50):
            value = generate_application_id()
            self.assertEqual(len(value), APP_ID_LENGTH)
            self.assertTrue(set(value).issubset(set(APP_ID_ALPHABET)))

    def test_alphabet_excludes_ambiguous_characters(self):
        for ch in "01OILo i l":
            self.assertNotIn(ch, APP_ID_ALPHABET)

    def test_save_assigns_unique_id(self):
        a = Application.objects.create()
        b = Application.objects.create()
        self.assertNotEqual(a.application_id, b.application_id)
        self.assertEqual(len(a.application_id), APP_ID_LENGTH)

    def test_save_does_not_overwrite_existing_id(self):
        a = Application.objects.create()
        original = a.application_id
        a.email = "foo@example.com"
        a.save()
        a.refresh_from_db()
        self.assertEqual(a.application_id, original)


class OtpTests(TestCase):
    def test_generate_otp_code_is_six_digits(self):
        for _ in range(20):
            code = generate_otp_code()
            self.assertEqual(len(code), 6)
            self.assertTrue(code.isdigit())

    def test_issue_and_verify_otp_success(self):
        app = Application.objects.create(email="user@example.com")
        code = app.issue_otp()
        self.assertEqual(len(code), 6)
        # Plaintext is never persisted.
        self.assertNotIn(code, app.otp_hash)
        self.assertTrue(app.verify_otp(code))
        app.refresh_from_db()
        self.assertEqual(app.otp_hash, "")
        self.assertIsNone(app.otp_expires_at)
        self.assertIsNotNone(app.email_verified_at)
        self.assertEqual(app.status, Application.Status.EMAIL_VERIFIED)

    def test_verify_otp_wrong_code_fails(self):
        app = Application.objects.create(email="user@example.com")
        app.issue_otp()
        self.assertFalse(app.verify_otp("000000"))
        app.refresh_from_db()
        # Hash still set; user can retry.
        self.assertTrue(app.otp_hash)
        self.assertIsNone(app.email_verified_at)

    def test_verify_otp_expired_fails(self):
        app = Application.objects.create(email="user@example.com")
        code = app.issue_otp()
        app.otp_expires_at = timezone.now() - timedelta(minutes=1)
        app.save(update_fields=["otp_expires_at"])
        self.assertFalse(app.verify_otp(code))

    def test_verify_otp_no_pending_fails(self):
        app = Application.objects.create(email="user@example.com")
        self.assertFalse(app.verify_otp("123456"))

    def test_verify_otp_attempt_cap(self):
        app = Application.objects.create(email="user@example.com")
        code = app.issue_otp()
        for _ in range(11):
            app.verify_otp("000000")
        # After cap, even the correct code is rejected.
        self.assertFalse(app.verify_otp(code))


class SiteSettingsTests(TestCase):
    def test_load_creates_singleton_with_default_message(self):
        obj = SiteSettings.load()
        self.assertEqual(obj.pk, 1)
        self.assertIn("Welcome", obj.welcome_message)

    def test_load_returns_existing_singleton(self):
        a = SiteSettings.load()
        a.welcome_message = "Hello!"
        a.save()
        b = SiteSettings.load()
        self.assertEqual(b.pk, 1)
        self.assertEqual(b.welcome_message, "Hello!")

    def test_save_forces_pk_one(self):
        s = SiteSettings(welcome_message="x")
        s.pk = 99
        s.save()
        self.assertEqual(s.pk, 1)
