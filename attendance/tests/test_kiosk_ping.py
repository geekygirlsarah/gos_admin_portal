"""Tests for the kiosk ping health-check endpoint."""

import json

from django.test import Client, TestCase
from django.urls import reverse

from attendance.models import KioskConfig

from .base import make_program


class KioskPingTests(TestCase):
    """GET /api/v1/kiosk/<id>/ping/ — lightweight health check."""

    def setUp(self):
        self.client = Client()
        self.program = make_program()
        self.kiosk = KioskConfig.objects.create(
            label="Ping Test Kiosk",
            program=self.program,
        )

    def test_ping_returns_ok(self):
        url = reverse("api_kiosk_ping", args=[self.kiosk.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertTrue(data["ok"])

    def test_ping_does_not_require_unlock_cookie(self):
        self.client.cookies.clear()
        url = reverse("api_kiosk_ping", args=[self.kiosk.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertTrue(data["ok"])

    def test_ping_inactive_kiosk_returns_404(self):
        self.kiosk.is_active = False
        self.kiosk.save()
        url = reverse("api_kiosk_ping", args=[self.kiosk.pk])
        response = self.client.get(url)
        self.assertIn(response.status_code, [302, 404])

    def test_ping_nonexistent_kiosk_returns_404(self):
        url = reverse("api_kiosk_ping", args=[99999])
        response = self.client.get(url)
        self.assertIn(response.status_code, [302, 404])

    def test_ping_rejects_post(self):
        url = reverse("api_kiosk_ping", args=[self.kiosk.pk])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 405)

    def test_kiosk_page_has_service_down_overlay(self):
        url = reverse("kiosk_signin", args=[self.kiosk.pk])
        self.client.cookies[f"kiosk_unlocked_{self.kiosk.pk}"] = "1"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("serviceDownOverlay", content)
        self.assertIn("Temporarily Unavailable", content)

    def test_kiosk_page_has_ping_function(self):
        url = reverse("kiosk_signin", args=[self.kiosk.pk])
        self.client.cookies[f"kiosk_unlocked_{self.kiosk.pk}"] = "1"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("/ping/", content)
        self.assertIn("scheduleReconnectPing", content)
