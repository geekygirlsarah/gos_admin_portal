"""Tests for throttling of the public application wizard (/apply/).

Covers the per-IP POST cap (middleware), the per-email OTP send cap and the
per-application OTP verify cap (view level), plus the 429 response shape
(Retry-After header + friendly template).

Throttling is disabled by default during the test run (the shared test-client
IP and process-local cache would otherwise leak one test's hits into the next),
so these tests explicitly re-enable it.
"""

from __future__ import annotations

import time

from django.contrib.auth.models import AnonymousUser
from django.core import mail
from django.core.cache import cache
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from applications.models import Application
from applications.rate_limiting import rate_limit_hit, rate_limited_response
from GoSAdminPortal.middleware import ApplyRateLimitMiddleware

LIMITS_ENABLED = {"TESTING": False, "APPLY_RATE_LIMIT_ENABLED": True}


class RateLimitHitTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_allows_up_to_limit_then_blocks(self):
        for _ in range(10):
            allowed, retry_after = rate_limit_hit("test", "basic", 10, 60)
            self.assertTrue(allowed)
            self.assertEqual(retry_after, 0)
        allowed, retry_after = rate_limit_hit("test", "basic", 10, 60)
        self.assertFalse(allowed)
        self.assertGreaterEqual(retry_after, 1)
        self.assertLessEqual(retry_after, 60)

    def test_scope_and_key_are_independent(self):
        for _ in range(10):
            rate_limit_hit("test", "a", 10, 60)
        # Different scope and different key are both still allowed.
        self.assertTrue(rate_limit_hit("test", "b", 10, 60)[0])
        self.assertTrue(rate_limit_hit("other", "a", 10, 60)[0])
        # Same scope/key is still blocked.
        self.assertFalse(rate_limit_hit("test", "a", 10, 60)[0])

    def test_window_resets(self):
        cache.set(
            "gos_rate_limit:test:expired",
            {"start": time.time() - 120, "count": 10},
            3600,
        )
        allowed, retry_after = rate_limit_hit("test", "expired", 10, 60)
        self.assertTrue(allowed)
        self.assertEqual(retry_after, 0)

    def test_window_reset_restores_full_budget(self):
        for _ in range(10):
            rate_limit_hit("test", "exhausted", 10, 60)
        cache.set(
            "gos_rate_limit:test:exhausted",
            {"start": time.time() - 120, "count": 10},
            3600,
        )
        for _ in range(10):
            self.assertTrue(rate_limit_hit("test", "exhausted", 10, 60)[0])


class RateLimitedResponseTests(TestCase):
    def setUp(self):
        self.request = RequestFactory().get("/apply/")
        self.request.user = AnonymousUser()

    def test_returns_429_with_retry_after_and_friendly_page(self):
        response = rate_limited_response(self.request, 45)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response["Retry-After"], "45")
        self.assertContains(response, "Just a moment", status_code=429)
        self.assertContains(response, "about 45 seconds", status_code=429)

    def test_retry_after_renders_friendly_minutes(self):
        response = rate_limited_response(self.request, 3540)
        self.assertEqual(response["Retry-After"], "3540")
        self.assertContains(response, "about 59 minutes", status_code=429)


@override_settings(**LIMITS_ENABLED)
class ApplyIpRateLimitMiddlewareTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_post_limit_exceeded_returns_429(self):
        url = reverse("apply_start")
        for _ in range(10):
            response = self.client.post(url)
            self.assertEqual(response.status_code, 302)  # new app started
        response = self.client.post(url)
        self.assertEqual(response.status_code, 429)
        self.assertTrue(response["Retry-After"])
        self.assertContains(response, "Just a moment", status_code=429)

    def test_get_requests_are_not_throttled(self):
        url = reverse("apply_start")
        for _ in range(15):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)

    def test_non_apply_post_is_not_throttled(self):
        factory = RequestFactory()
        middleware = ApplyRateLimitMiddleware(lambda request: None)
        for _ in range(15):
            request = factory.post("/programs/students/")
            request.user = AnonymousUser()
            self.assertIsNone(middleware(request))

    def test_middleware_returns_429_directly(self):
        factory = RequestFactory()
        middleware = ApplyRateLimitMiddleware(lambda request: None)
        for _ in range(10):
            request = factory.post("/apply/")
            request.user = AnonymousUser()
            self.assertIsNone(middleware(request))
        request = factory.post("/apply/")
        request.user = AnonymousUser()
        response = middleware(request)
        self.assertEqual(response.status_code, 429)
        self.assertIn("Retry-After", response)

    def test_middleware_skips_when_rate_limiting_disabled(self):
        factory = RequestFactory()
        middleware = ApplyRateLimitMiddleware(lambda request: None)
        with self.settings(APPLY_RATE_LIMIT_ENABLED=False):
            for _ in range(15):
                request = factory.post("/apply/")
                request.user = AnonymousUser()
                self.assertIsNone(middleware(request))


class ApplyRateLimitDisabledTests(TestCase):
    """With default test settings throttling is off entirely."""

    def setUp(self):
        cache.clear()

    def test_apply_posts_not_throttled_when_disabled(self):
        for _ in range(15):
            response = self.client.post(reverse("apply_start"))
            self.assertEqual(response.status_code, 302)


@override_settings(**LIMITS_ENABLED)
class OtpSendRateLimitTests(TestCase):
    def setUp(self):
        cache.clear()
        mail.outbox = []

    def test_otp_send_limit_exceeded_returns_429(self):
        app = Application.objects.create(email="kid@example.com")
        url = reverse("apply_step3_resend", kwargs={"app_id": app.application_id})
        for _ in range(5):
            response = self.client.post(url)
            self.assertEqual(response.status_code, 302)
        response = self.client.post(url)
        self.assertEqual(response.status_code, 429)
        self.assertTrue(response["Retry-After"])
        self.assertContains(response, "Just a moment", status_code=429)

    def test_otp_send_limit_is_independent_per_email(self):
        app_one = Application.objects.create(email="one@example.com")
        app_two = Application.objects.create(email="two@example.com")
        url_one = reverse(
            "apply_step3_resend", kwargs={"app_id": app_one.application_id}
        )
        url_two = reverse(
            "apply_step3_resend", kwargs={"app_id": app_two.application_id}
        )
        for _ in range(5):
            self.client.post(url_one)
        # A different email is still allowed...
        self.assertEqual(self.client.post(url_two).status_code, 302)
        # ...while the exhausted email is now blocked.
        self.assertEqual(self.client.post(url_one).status_code, 429)

    def test_step2_otp_issuance_counts_toward_send_limit(self):
        app = Application.objects.create()
        url = reverse("apply_step2", kwargs={"app_id": app.application_id})
        for _ in range(5):
            response = self.client.post(
                url, {"applicant_type": "parent", "email": "spam@example.com"}
            )
            self.assertEqual(response.status_code, 302)
            # Clear the pending code so the next POST issues again.
            app.refresh_from_db()
            app.otp_hash = ""
            app.otp_expires_at = None
            app.save(update_fields=["otp_hash", "otp_expires_at"])
        response = self.client.post(
            url, {"applicant_type": "parent", "email": "spam@example.com"}
        )
        self.assertEqual(response.status_code, 429)

    def test_step3_get_otp_issuance_counts_toward_send_limit(self):
        app = Application.objects.create(
            applicant_type="parent",
            email="spam2@example.com",
            current_step=3,
        )
        url = reverse("apply_step3", kwargs={"app_id": app.application_id})
        for _ in range(5):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            app.refresh_from_db()
            app.otp_hash = ""
            app.otp_expires_at = None
            app.save(update_fields=["otp_hash", "otp_expires_at"])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 429)


@override_settings(**LIMITS_ENABLED, APPLY_IP_POST_LIMIT=100)
class OtpVerifyRateLimitTests(TestCase):
    def setUp(self):
        cache.clear()

    def _app(self):
        app = Application.objects.create(
            applicant_type="parent",
            email="parent@example.com",
            current_step=3,
        )
        app.issue_otp()
        return app

    def test_otp_verify_limit_exceeded_returns_429(self):
        app = self._app()
        url = reverse("apply_step3", kwargs={"app_id": app.application_id})
        for _ in range(10):
            response = self.client.post(url, {"code": "000000"})
            self.assertEqual(response.status_code, 200)  # wrong code re-renders
        response = self.client.post(url, {"code": "000000"})
        self.assertEqual(response.status_code, 429)
        self.assertTrue(response["Retry-After"])

    def test_otp_verify_limit_is_independent_per_application(self):
        app_one = self._app()
        app_two = self._app()
        url_one = reverse("apply_step3", kwargs={"app_id": app_one.application_id})
        url_two = reverse("apply_step3", kwargs={"app_id": app_two.application_id})
        for _ in range(10):
            self.client.post(url_one, {"code": "000000"})
        self.assertEqual(self.client.post(url_one, {"code": "000000"}).status_code, 429)
        # A different application is unaffected.
        response = self.client.post(url_two, {"code": "000000"})
        self.assertEqual(response.status_code, 200)


@override_settings(TESTING=False, APPLY_RATE_LIMIT_ENABLED=False)
class OtpLimitDisabledTests(TestCase):
    """Even outside the test suite, a disabled limiter must never trip."""

    def setUp(self):
        cache.clear()

    def test_otp_send_never_blocked_when_disabled(self):
        app = Application.objects.create(email="kid@example.com")
        url = reverse("apply_step3_resend", kwargs={"app_id": app.application_id})
        for _ in range(15):
            response = self.client.post(url)
            self.assertEqual(response.status_code, 302)
