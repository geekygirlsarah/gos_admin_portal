import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.signals import request_finished
from django.db import OperationalError
from django.db.backends.base.base import BaseDatabaseWrapper
from django.test import RequestFactory, TestCase, override_settings

from GoSAdminPortal.middleware import MaintenanceModeMiddleware
from GoSAdminPortal.views import health


@override_settings(ALLOWED_HOSTS=["testserver", ".localhost"])
class MaintenanceModeTests(TestCase):
    @override_settings(MAINTENANCE_MODE=True)
    def test_all_page_methods_and_tenant_hosts_receive_maintenance_response(self):
        requests = (
            ("get", "/", "testserver"),
            ("post", "/programs/", "gos.localhost"),
            ("head", "/accounts/login/", "testserver"),
        )

        for method, path, host in requests:
            with self.subTest(method=method, path=path, host=host):
                response = getattr(self.client, method)(path, HTTP_HOST=host)
                self.assertEqual(response.status_code, 503)
                self.assertIn("no-store", response.headers.get("Cache-Control", ""))
                self.assertEqual(
                    response.headers.get("X-Content-Type-Options"), "nosniff"
                )
                if method != "head":
                    self.assertContains(
                        response,
                        "The server is temporarily down in order to do some maintenance. The site will be back up within a few minutes.",
                        status_code=503,
                    )

    @override_settings(MAINTENANCE_MODE=True)
    def test_maintenance_response_does_not_query_the_database(self):
        with self.assertNumQueries(0):
            response = self.client.get("/profile/", HTTP_HOST="gos.localhost")
        self.assertEqual(response.status_code, 503)

    @override_settings(MAINTENANCE_MODE=True)
    def test_maintenance_mode_does_not_bypass_for_superusers(self):
        user = get_user_model().objects.create_superuser(  # nosec B106
            username="maintenance-admin",
            email="admin@example.test",
            password="test-pass",
        )
        self.client.force_login(user)

        response = self.client.get("/platform-admin/", HTTP_HOST="testserver")

        self.assertEqual(response.status_code, 503)
        self.assertContains(
            response,
            "The server is temporarily down in order to do some maintenance. The site will be back up within a few minutes.",
            status_code=503,
        )

    @override_settings(MAINTENANCE_MODE=True)
    def test_real_health_check_still_reports_database_failure(self):
        request = RequestFactory().get("/health/")
        with patch.object(
            BaseDatabaseWrapper,
            "cursor",
            side_effect=OperationalError("database unavailable"),
        ):
            response = MaintenanceModeMiddleware(health)(request)

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.content, b'{"status": "unhealthy", "db": "unavailable"}'
        )

    @override_settings(MAINTENANCE_MODE=True)
    def test_real_health_endpoint_remains_available(self):
        response = self.client.get("/health/", HTTP_HOST="testserver")

        self.assertIn(response.status_code, (200, 503))
        self.assertIn("db", response.json())
        self.assertNotIn(b"Portal maintenance in progress", response.content)

    @override_settings(MAINTENANCE_MODE=True)
    def test_static_assets_remain_available(self):
        with tempfile.TemporaryDirectory() as static_root:
            asset = Path(static_root) / "maintenance-probe.css"
            asset.write_text("body { color: black; }", encoding="utf-8")
            with override_settings(
                STATIC_ROOT=static_root,
                SECURE_SSL_REDIRECT=True,
            ):
                http_response = self.client.get(
                    "/static/maintenance-probe.css", HTTP_HOST="testserver"
                )
                self.assertEqual(http_response.status_code, 301)
                self.assertEqual(
                    http_response.headers["Location"],
                    "https://testserver/static/maintenance-probe.css",
                )

                finished_requests = []

                def record_request_finished(**kwargs):
                    finished_requests.append(True)

                request_finished.connect(record_request_finished, weak=False)
                try:
                    response = self.client.get(
                        "/static/maintenance-probe.css",
                        secure=True,
                        HTTP_HOST="testserver",
                    )
                    response_content = b"".join(response.streaming_content)
                finally:
                    request_finished.disconnect(record_request_finished)

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/css", response.headers.get("Content-Type", ""))
        self.assertIn(b"color: black", response_content)
        self.assertEqual(len(finished_requests), 1)

    @override_settings(MAINTENANCE_MODE=False)
    def test_disabled_mode_restores_normal_application_and_health_endpoints(self):
        login_response = self.client.get("/accounts/login/", HTTP_HOST="testserver")
        health_response = self.client.get("/health/", HTTP_HOST="testserver")

        self.assertEqual(login_response.status_code, 200)
        self.assertNotContains(
            login_response,
            "The server is temporarily down in order to do some maintenance. The site will be back up within a few minutes.",
            status_code=200,
        )
        self.assertIn(health_response.status_code, (200, 503))
