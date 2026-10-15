import datetime
import logging
import os
from contextlib import contextmanager
from unittest import mock

from asgiref.sync import async_to_sync, sync_to_async
from django.conf import settings
from django.contrib.auth.models import AnonymousUser, User
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from audit.middleware import AuditHistoryMiddleware
from GoSAdminPortal.adapter import _find_or_provision_user_for_email
from GoSAdminPortal.middleware import LoginRequiredMiddleware, MentorAgreementMiddleware
from GoSAdminPortal.views import handler500


@contextmanager
def _silenced_django_request_logs():
    """Suppress ``django.request`` ERROR lines (e.g. deliberate 503s).

    Some tests intentionally trigger 5xx responses (like the health check's
    "unhealthy" paths), which Django logs at ERROR and which would otherwise
    pollute the test output with scary but expected tracebacks.
    """
    logger = logging.getLogger("django.request")
    logger.disabled = True
    try:
        yield
    finally:
        logger.disabled = False


class MiddlewareAsyncTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user(username="testuser")

    def test_sync_middleware_auth(self):
        def get_response(request):
            return HttpResponse("OK")

        middleware = LoginRequiredMiddleware(get_response)
        request = self.factory.get("/programs/")
        request.user = self.user
        response = middleware(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"OK")

    def test_sync_middleware_anon(self):
        def get_response(request):
            return HttpResponse("OK")

        middleware = LoginRequiredMiddleware(get_response)
        request = self.factory.get("/programs/")
        request.user = AnonymousUser()
        response = middleware(request)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    def test_sync_middleware_exempt(self):
        def get_response(request):
            return HttpResponse("OK")

        middleware = LoginRequiredMiddleware(get_response)
        request = self.factory.get("/accounts/login/")
        request.user = AnonymousUser()
        response = middleware(request)
        self.assertEqual(response.status_code, 200)

    async def test_async_middleware_auth(self):
        async def get_response(request):
            return HttpResponse("OK")

        middleware = LoginRequiredMiddleware(async_to_sync(get_response))
        request = self.factory.get("/programs/")
        request.user = self.user
        response = await sync_to_async(middleware, thread_sensitive=True)(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"OK")

    async def test_async_middleware_anon(self):
        async def get_response(request):
            return HttpResponse("OK")

        middleware = LoginRequiredMiddleware(async_to_sync(get_response))
        request = self.factory.get("/programs/")
        request.user = AnonymousUser()
        response = await sync_to_async(middleware, thread_sensitive=True)(request)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    async def test_async_middleware_exempt(self):
        async def get_response(request):
            return HttpResponse("OK")

        middleware = LoginRequiredMiddleware(async_to_sync(get_response))
        request = self.factory.get("/accounts/login/")
        request.user = AnonymousUser()
        response = await sync_to_async(middleware, thread_sensitive=True)(request)
        self.assertEqual(response.status_code, 200)

    def test_sync_middleware_unknown_path_redirects_to_login(self):
        """TDD for Issue 8: an unresolvable path should redirect anonymous
        users to login instead of being treated as exempt (which previously
        let the 404 handler take over without asking the user to log in).
        """

        def get_response(request):
            return HttpResponse("OK")

        middleware = LoginRequiredMiddleware(get_response)
        request = self.factory.get("/this-path-does-not-exist/")
        request.user = AnonymousUser()
        response = middleware(request)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])


class MiddlewareExemptionTests(TestCase):
    def test_apply_is_exempt(self):
        # Anonymous user hitting /apply/ (apply_start)
        response = self.client.get(reverse("apply_start"))
        # Should NOT be redirected to login
        self.assertNotEqual(response.status_code, 302)
        # It might be 200 or 302 (if it redirects to another step), but NOT to login
        if response.status_code == 302:
            self.assertNotIn("/accounts/login/", response.url)

    def test_login_is_exempt(self):
        # Anonymous user hitting /accounts/login/
        response = self.client.get(reverse("account_login"))
        self.assertEqual(response.status_code, 200)

    def test_regular_page_is_not_exempt(self):
        # Anonymous user hitting /programs/students/
        response = self.client.get(reverse("student_list"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)

    def test_privacy_policy_is_exempt(self):
        # Anonymous user hitting /privacy/
        response = self.client.get(reverse("privacy_policy"))
        self.assertEqual(response.status_code, 200)

    def test_non_discrimination_policy_is_exempt(self):
        # Anonymous user hitting /non-discrimination/
        response = self.client.get(reverse("non_discrimination_policy"))
        self.assertEqual(response.status_code, 200)


class HealthCheckViewTest(TestCase):
    """Tests for the /health endpoint used by Render health checks."""

    def setUp(self):
        self.factory = RequestFactory()
        from django.core.cache import cache

        cache.clear()

    def test_health_returns_200(self):
        response = self.client.get(reverse("health"))
        self.assertEqual(response.status_code, 200)

    def test_health_reports_ok_status(self):
        response = self.client.get(reverse("health"))
        self.assertJSONEqual(
            response.content, {"status": "ok", "db": "ok", "email": "ok"}
        )

    def test_health_anonymous_access(self):
        """Anonymous users must be able to hit /health (Render probe)."""
        response = self.client.get(reverse("health"))
        self.assertNotEqual(response.status_code, 302)
        self.assertEqual(response.status_code, 200)

    def test_health_exempt_from_login_middleware(self):
        """Middleware should not redirect anonymous /health requests to login."""

        def get_response(request):
            return HttpResponse("OK")

        middleware = LoginRequiredMiddleware(get_response)
        request = self.factory.get("/health/")
        request.user = AnonymousUser()
        response = middleware(request)
        self.assertEqual(response.status_code, 200)

    def test_health_anonymous_access_no_trailing_slash(self):
        """Anonymous /health (no trailing slash) must hit the real endpoint.

        Probes (e.g. Render) may hit /health without a slash. That path used to
        be redirected to login, which the probe followed and then treated the
        login page's 200 as "healthy" without ever running the check.
        """
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(
            response.content, {"status": "ok", "db": "ok", "email": "ok"}
        )

    def test_health_exempt_from_login_middleware_no_trailing_slash(self):
        """Middleware should not redirect anonymous /health (no slash) to login."""

        def get_response(request):
            return HttpResponse("OK")

        middleware = LoginRequiredMiddleware(get_response)
        request = self.factory.get("/health")
        request.user = AnonymousUser()
        response = middleware(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"OK")

    def test_health_reports_db_down(self):
        """When the DB connection is broken, /health returns 503."""
        from unittest import mock

        with _silenced_django_request_logs():
            with mock.patch("GoSAdminPortal.views.connection") as mock_conn:
                mock_conn.cursor.side_effect = Exception("DB connection failed")
                response = self.client.get(reverse("health"))
                self.assertEqual(response.status_code, 503)
                self.assertJSONEqual(
                    response.content, {"status": "unhealthy", "db": "unavailable"}
                )
        """When cursor creation or query raises, /health returns 503."""
        from unittest import mock

        with _silenced_django_request_logs():
            with mock.patch("GoSAdminPortal.views.connection") as mock_conn:
                mock_conn.cursor.side_effect = Exception("DB connection refused")
                response = self.client.get(reverse("health"))
                self.assertEqual(response.status_code, 503)
                self.assertJSONEqual(
                    response.content, {"status": "unhealthy", "db": "unavailable"}
                )

    def test_health_reports_email_down(self):
        """When the email backend is unreachable, /health returns 503."""
        from unittest import mock

        with _silenced_django_request_logs():
            with mock.patch("GoSAdminPortal.views.connection") as mock_conn:
                mock_conn.cursor.return_value.__enter__.return_value.fetchone.return_value = (
                    1,
                )
                with mock.patch("GoSAdminPortal.views.mail") as mock_mail:
                    mock_mail.mailers.default.open.side_effect = Exception(
                        "SMTP connection refused"
                    )
                    response = self.client.get(reverse("health"))
                    self.assertEqual(response.status_code, 503)
                    self.assertJSONEqual(
                        response.content,
                        {"status": "unhealthy", "db": "ok", "email": "unavailable"},
                    )

    def test_health_reports_all_ok(self):
        """When DB and email are both healthy, /health returns 200 with details."""
        from unittest import mock

        with mock.patch("GoSAdminPortal.views.connection") as mock_conn:
            mock_conn.cursor.return_value.__enter__.return_value.fetchone.return_value = (
                1,
            )
            with mock.patch("GoSAdminPortal.views.mail") as mock_mail:
                mock_mail.mailers.default.open.return_value = True
                response = self.client.get(reverse("health"))
                self.assertEqual(response.status_code, 200)
                self.assertJSONEqual(
                    response.content,
                    {"status": "ok", "db": "ok", "email": "ok"},
                )

    def test_health_email_check_throttled_by_cache(self):
        """Repeated probes within the cache window must not re-ping SMTP.

        Render hits /health every 5 seconds; the email backend should only be
        contacted once per cache window instead of on every single probe.
        """
        from unittest import mock

        with mock.patch("GoSAdminPortal.views.connection") as mock_conn:
            mock_conn.cursor.return_value.__enter__.return_value.fetchone.return_value = (
                1,
            )
            with mock.patch("GoSAdminPortal.views.mail") as mock_mail:
                mock_mail.mailers.default.open.return_value = True
                self.client.get(reverse("health"))
                self.client.get(reverse("health"))
                self.client.get(reverse("health"))
                self.assertEqual(mock_mail.mailers.default.open.call_count, 1)

    def test_health_email_check_reruns_after_cache_expiry(self):
        """Once the cached result expires, the next probe pings SMTP again."""
        from unittest import mock

        from django.core.cache import cache

        with mock.patch("GoSAdminPortal.views.connection") as mock_conn:
            mock_conn.cursor.return_value.__enter__.return_value.fetchone.return_value = (
                1,
            )
            with mock.patch("GoSAdminPortal.views.mail") as mock_mail:
                mock_mail.mailers.default.open.return_value = True
                self.client.get(reverse("health"))
                cache.clear()
                self.client.get(reverse("health"))
                self.assertEqual(mock_mail.mailers.default.open.call_count, 2)


class AdapterEmailProvisioningTest(TestCase):
    """Tests for _find_or_provision_user_for_email in GoSAdminPortal/adapter.py."""

    def _make_adult(self, email, **kwargs):
        from programs.models import Adult

        return Adult.objects.create(
            legal_first_name=kwargs.get("legal_first_name", "Ada"),
            last_name=kwargs.get("last_name", "Lovelace"),
            personal_email=email,
            login_enabled=True,
        )

    def _make_student(self, personal_email=None, andrew_email=None):
        from programs.models import Student

        return Student.objects.create(
            legal_first_name="Grace",
            last_name="Hopper",
            date_of_birth=datetime.date(2010, 1, 1),
            personal_email=personal_email,
            andrew_email=andrew_email,
        )

    # ── existing User ────────────────────────────────────────────────────────

    def test_existing_user_email_allowed(self):
        User.objects.create_user(username="known", email="known@example.com")
        self.assertTrue(_find_or_provision_user_for_email("known@example.com"))

    def test_unknown_email_rejected(self):
        self.assertFalse(_find_or_provision_user_for_email("nobody@example.com"))

    # ── Adult provisioning ───────────────────────────────────────────────────

    def test_adult_email_allowed_no_user(self):
        """Adult with no linked User: a new User should be provisioned."""
        from programs.models import Adult

        adult = self._make_adult("parent@example.com")
        result = _find_or_provision_user_for_email("parent@example.com")
        self.assertTrue(result)
        adult.refresh_from_db()
        self.assertIsNotNone(adult.user_id)
        self.assertTrue(User.objects.filter(email="parent@example.com").exists())

    def test_adult_email_allowed_existing_user(self):
        """Adult already linked to a User: allowed without creating a new User."""
        user = User.objects.create_user(
            username="adultuser", email="adultuser@example.com"
        )
        adult = self._make_adult("adultuser@example.com", legal_first_name="Ada")
        adult.user = user
        adult.save(update_fields=["user"])
        result = _find_or_provision_user_for_email("adultuser@example.com")
        self.assertTrue(result)
        self.assertEqual(User.objects.filter(email="adultuser@example.com").count(), 1)

    def test_adult_email_case_insensitive(self):
        self._make_adult("Parent@Example.COM")
        self.assertTrue(_find_or_provision_user_for_email("parent@example.com"))

    # ── Student provisioning ─────────────────────────────────────────────────

    def test_student_personal_email_allowed(self):
        """Student personal_email: a new User should be provisioned."""
        from programs.models import Student

        student = self._make_student(personal_email="grace@personal.com")
        result = _find_or_provision_user_for_email("grace@personal.com")
        self.assertTrue(result)
        student.refresh_from_db()
        self.assertIsNotNone(student.user_id)

    def test_student_andrew_email_allowed(self):
        """Student andrew_email: a new User should be provisioned."""
        from programs.models import Student

        student = self._make_student(andrew_email="ghopper@andrew.cmu.edu")
        result = _find_or_provision_user_for_email("ghopper@andrew.cmu.edu")
        self.assertTrue(result)
        student.refresh_from_db()
        self.assertIsNotNone(student.user_id)

    def test_student_email_case_insensitive(self):
        self._make_student(personal_email="Grace@Personal.COM")
        self.assertTrue(_find_or_provision_user_for_email("grace@personal.com"))

    def test_student_existing_user_not_duplicated(self):
        """Student already has a User: no new User created."""
        user = User.objects.create_user(username="stuuser", email="stu@personal.com")
        from programs.models import Student

        student = self._make_student(personal_email="stu@personal.com")
        student.user = user
        student.save(update_fields=["user"])
        result = _find_or_provision_user_for_email("stu@personal.com")
        self.assertTrue(result)
        self.assertEqual(User.objects.filter(email="stu@personal.com").count(), 1)

    def test_adult_andrew_email_allowed(self):
        """Adult andrew_email can also be used to log in."""
        from programs.models import Adult

        Adult.objects.create(
            legal_first_name="Mentor",
            last_name="Smith",
            is_mentor=True,
            andrew_email="msmith@andrew.cmu.edu",
            login_enabled=True,
        )
        self.assertTrue(_find_or_provision_user_for_email("msmith@andrew.cmu.edu"))

    # ── allauth EmailAddress record ──────────────────────────────────────────

    def test_allauth_email_address_record_created(self):
        """Provisioning an adult creates an allauth EmailAddress record."""
        from allauth.account.models import EmailAddress

        self._make_adult("newparent@example.com")
        _find_or_provision_user_for_email("newparent@example.com")
        self.assertTrue(
            EmailAddress.objects.filter(email="newparent@example.com").exists()
        )


class LoginPolicyByRoleTest(TestCase):
    """TDD for role-based login identifier rules.

    Matrix to enforce:
      - Students: Andrew or personal email allowed
      - Parents: personal email only (Andrew email denied)
      - Mentors: Andrew email only (personal email denied)
      - Alumni: personal email only (Andrew email denied)
      - Lead Mentors: same as mentors (Andrew email only)
    """

    def _make_adult(self, **kwargs):
        from programs.models import Adult

        defaults = dict(
            legal_first_name="Ada",
            last_name="Lovelace",
        )
        defaults.update(kwargs)
        return Adult.objects.create(**defaults)

    def _make_student(self, **kwargs):
        from programs.models import Student

        defaults = dict(
            legal_first_name="Grace",
            last_name="Hopper",
            date_of_birth=datetime.date(2010, 1, 1),
        )
        defaults.update(kwargs)
        return Student.objects.create(**defaults)

    # ── Mentors ─────────────────────────────────────────────────────────────

    def test_mentor_personal_email_denied(self):
        self._make_adult(is_mentor=True, personal_email="mentor.personal@example.com")
        allowed = _find_or_provision_user_for_email("mentor.personal@example.com")
        self.assertFalse(allowed)

    def test_mentor_andrew_email_allowed(self):
        self._make_adult(is_mentor=True, andrew_email="mentor1@andrew.cmu.edu")
        allowed = _find_or_provision_user_for_email("mentor1@andrew.cmu.edu")
        self.assertTrue(allowed)

    # ── Lead Mentors (same rule as mentors) ─────────────────────────────────

    def test_lead_mentor_andrew_only(self):
        # Represent lead mentor as a mentor adult; group membership is handled separately.
        self._make_adult(
            is_mentor=True,
            andrew_email="leadmentor@andrew.cmu.edu",
            personal_email="leadmentor.personal@example.com",
        )
        self.assertTrue(_find_or_provision_user_for_email("leadmentor@andrew.cmu.edu"))
        self.assertFalse(
            _find_or_provision_user_for_email("leadmentor.personal@example.com")
        )

    # ── Parents ─────────────────────────────────────────────────────────────

    def test_parent_personal_email_allowed(self):
        self._make_adult(is_parent=True, personal_email="parent@example.com")
        self.assertTrue(_find_or_provision_user_for_email("parent@example.com"))

    def test_parent_andrew_email_denied(self):
        self._make_adult(is_parent=True, andrew_email="parent1@andrew.cmu.edu")
        self.assertFalse(_find_or_provision_user_for_email("parent1@andrew.cmu.edu"))

    # ── Alumni ──────────────────────────────────────────────────────────────

    def test_alumni_personal_email_allowed(self):
        self._make_adult(is_alumni=True, personal_email="alumni@example.com")
        self.assertTrue(_find_or_provision_user_for_email("alumni@example.com"))

    def test_alumni_andrew_email_denied(self):
        self._make_adult(is_alumni=True, andrew_email="grad@andrew.cmu.edu")
        self.assertFalse(_find_or_provision_user_for_email("grad@andrew.cmu.edu"))

    # ── Students ────────────────────────────────────────────────────────────

    def test_student_allows_personal_and_andrew(self):
        self._make_student(
            personal_email="student.personal@example.com",
            andrew_email="student1@andrew.cmu.edu",
        )
        self.assertTrue(
            _find_or_provision_user_for_email("student.personal@example.com")
        )
        self.assertTrue(_find_or_provision_user_for_email("student1@andrew.cmu.edu"))


class _ExplodingUser:
    """Stand-in for ``request.user`` that fails on any attribute access.

    Exempt paths (media/static/health/...) must be decided *before* auth
    state is read: touching ``request.user`` loads the session from the
    database, and a photo grid fires dozens of concurrent ``/media/``
    requests — that pattern exhausted Postgres connection slots in
    production (``remaining connection slots are reserved for
    roles with the SUPERUSER attribute``).
    """

    @property
    def is_authenticated(self):
        raise AssertionError("request.user accessed for an exempt path")


class _ExplodingSession:
    """Stand-in for ``request.session`` that fails on any read."""

    def get(self, *args, **kwargs):
        raise AssertionError("request.session accessed for an exempt path")


class ExemptPathNoAuthSessionTests(TestCase):
    """Exempt paths must not touch ``request.user``/``request.session``."""

    def setUp(self):
        self.factory = RequestFactory()

    @staticmethod
    def _passthrough(request):
        return HttpResponse("OK")

    def _exempt_request(self, path):
        request = self.factory.get(path)
        request.user = _ExplodingUser()
        request.session = _ExplodingSession()
        return request

    def test_login_required_media_path_skips_user_and_session(self):
        middleware = LoginRequiredMiddleware(self._passthrough)
        response = middleware(self._exempt_request("/media/photos/students/x.jpg"))
        self.assertEqual(response.status_code, 200)

    def test_login_required_static_path_skips_user_and_session(self):
        middleware = LoginRequiredMiddleware(self._passthrough)
        response = middleware(self._exempt_request("/static/css/main.css"))
        self.assertEqual(response.status_code, 200)

    def test_login_required_health_path_skips_user_and_session(self):
        middleware = LoginRequiredMiddleware(self._passthrough)
        response = middleware(self._exempt_request("/health/"))
        self.assertEqual(response.status_code, 200)

    def test_mentor_agreement_media_path_skips_user_and_session(self):
        # MENTOR_AGREEMENT_ENABLED is off during tests by default, so force
        # it on to exercise the real code path that reads request.user.
        with override_settings(MENTOR_AGREEMENT_ENABLED=True):
            middleware = MentorAgreementMiddleware(self._passthrough)
            response = middleware(self._exempt_request("/media/photos/students/x.jpg"))
        self.assertEqual(response.status_code, 200)

    def test_timezone_middleware_removed(self):
        # Nothing ever wrote session["django_timezone"], so the middleware
        # only ever read the session (a DB query per request) without
        # effect. Times display via settings.TIME_ZONE instead.
        self.assertNotIn(
            "GoSAdminPortal.middleware.TimezoneMiddleware", settings.MIDDLEWARE
        )
        from GoSAdminPortal import middleware as portal_middleware

        self.assertFalse(hasattr(portal_middleware, "TimezoneMiddleware"))


class AuditHistoryMediaPathTests(TestCase):
    """pghistory's middleware reads user/session while building its context.

    On Postgres it must skip media/static paths so concurrent file requests
    stay DB-free.
    """

    def test_media_path_does_not_build_pghistory_context(self):
        request = RequestFactory().get("/media/photos/students/x.jpg")
        request.user = _ExplodingUser()

        def get_response(req):
            return HttpResponse("OK")

        middleware = AuditHistoryMiddleware(get_response)
        with mock.patch("audit.middleware.connection") as mock_connection:
            mock_connection.vendor = "postgresql"
            response = middleware(request)
        self.assertEqual(response.status_code, 200)


class MediaRequestDbFreeTests(TestCase):
    """A media request must perform zero DB queries end-to-end.

    Production incident: each concurrent ``/media/`` GET opened its own
    session/user DB connection (requests run in per-request threads), and a
    photo grid exhausted Postgres connection slots.
    """

    MEDIA_REL = os.path.join("photos", "students", "_db_free_probe.jpg")

    def setUp(self):
        full_path = os.path.join(settings.MEDIA_ROOT, self.MEDIA_REL)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "wb") as probe:
            probe.write(b"\xff\xd8\xff\xe0db-free-probe")
        self.addCleanup(self._remove_probe, full_path)

        user = User.objects.create_user(username="media_user")
        self.client.force_login(user)
        # Prime OrganizationMiddleware's process-wide cache so its one-time
        # org lookup happens outside the assertNumQueries() block below.
        self.client.get("/health/")

    @staticmethod
    def _remove_probe(path):
        if os.path.exists(path):
            os.remove(path)

    def test_media_get_performs_zero_queries(self):
        url = settings.MEDIA_URL + self.MEDIA_REL.replace(os.sep, "/")
        with self.assertNumQueries(0):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            # Exhausting the streaming iterator fires response.close(),
            # releasing the file handle before cleanup deletes it.
            b"".join(response.streaming_content)


class Handler500RenderTests(TestCase):
    """The 500 page must render while the DB is down.

    request.user, sessions, and context processors are all unavailable
    during a database outage — the error page must not depend on them.
    """

    def test_handler500_does_not_touch_the_request(self):
        class ExplodingRequest:
            def __getattr__(self, attr):
                raise AssertionError(
                    f"request.{attr} accessed while rendering 500.html"
                )

        response = handler500(ExplodingRequest())
        self.assertEqual(response.status_code, 500)
        self.assertIn(b"500 Server Error", response.content)
