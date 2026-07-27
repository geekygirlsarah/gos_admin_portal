"""
Tests for the kiosk attendance system:
- KioskConfig model
- GET /api/v1/attendance/student/lookup endpoint
- Kiosk page views (public, no login required)
- KioskConfig management in portal settings
- Server-side proxy endpoints (unlock, lock, tap, lookup)
"""

import json

from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse

from attendance.models import KioskConfig, RFIDCard
from programs.models import Adult, Program, ProgramFeature, Student


class KioskConfigModelTests(TestCase):
    def setUp(self):
        self.program = Program.objects.create(name="Test Program")
        feat, _ = ProgramFeature.objects.get_or_create(
            key="attendance", defaults={"name": "Attendance"}
        )
        self.program.features.add(feat)

    def test_kiosk_config_str(self):
        config = KioskConfig.objects.create(
            label="Build Space Kiosk",
            program=self.program,
        )
        self.assertIn("Build Space Kiosk", str(config))

    def test_kiosk_config_defaults(self):
        config = KioskConfig.objects.create(
            label="Kiosk",
            program=self.program,
        )
        self.assertTrue(config.is_active)

    def test_kiosk_config_requires_label_and_program(self):
        # Creating without a program should fail
        with self.assertRaises(Exception):
            KioskConfig.objects.create(label="X")


class StudentLookupAPITests(TestCase):
    """Tests for GET /api/v1/attendance/student/lookup"""

    def setUp(self):
        from api.models import ApiClientKey

        self.client = Client()
        self.api_key = ApiClientKey.objects.create(
            name="Test Key",
            key="lookuptestkey1234567890abcdef",  # nosec B106
            scope=ApiClientKey.SCOPE_READ,
        )
        self.student = Student.objects.create(
            legal_first_name="Alice",
            first_name="Ali",
            last_name="Smith",
        )
        self.rfid = RFIDCard.objects.create(uid="RFID001", student=self.student)
        self.url = "/api/v1/attendance/student/lookup"

    def _auth_headers(self):
        return {"HTTP_X_API_KEY": self.api_key.key}

    def test_lookup_requires_api_key(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 401)

    def test_lookup_by_rfid(self):
        response = self.client.get(
            self.url, {"rfid": "RFID001"}, **self._auth_headers()
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("students", data)
        self.assertEqual(len(data["students"]), 1)
        self.assertEqual(data["students"][0]["id"], self.student.id)

    def test_lookup_by_name(self):
        response = self.client.get(
            self.url, {"name": "Alice Smith"}, **self._auth_headers()
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("students", data)
        self.assertEqual(len(data["students"]), 1)
        self.assertEqual(data["students"][0]["id"], self.student.id)

    def test_lookup_by_name_partial(self):
        # Searching by first name only should find the student
        response = self.client.get(self.url, {"name": "Alice"}, **self._auth_headers())
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("students", data)
        self.assertGreaterEqual(len(data["students"]), 1)

    def test_lookup_by_preferred_name(self):
        # Should match on first_name (preferred) too
        response = self.client.get(
            self.url, {"name": "Ali Smith"}, **self._auth_headers()
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("students", data)
        self.assertEqual(len(data["students"]), 1)

    def test_lookup_no_results(self):
        response = self.client.get(
            self.url, {"name": "Zzzz Yyyyy"}, **self._auth_headers()
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["students"], [])

    def test_lookup_no_params_returns_empty(self):
        response = self.client.get(self.url, **self._auth_headers())
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["students"], [])

    def test_lookup_returns_name_and_id(self):
        response = self.client.get(
            self.url, {"rfid": "RFID001"}, **self._auth_headers()
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        student_data = data["students"][0]
        self.assertIn("id", student_data)
        self.assertIn("name", student_data)

    def test_lookup_inactive_rfid_not_matched(self):
        self.rfid.is_active = False
        self.rfid.save()
        response = self.client.get(
            self.url, {"rfid": "RFID001"}, **self._auth_headers()
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["students"], [])

    def test_lookup_only_get_allowed(self):
        response = self.client.post(
            self.url,
            data=json.dumps({"rfid": "RFID001"}),
            content_type="application/json",
            **self._auth_headers(),
        )
        self.assertEqual(response.status_code, 405)


class KioskPageViewTests(TestCase):
    """Tests for the public kiosk sign-in page at /kiosk/<id>/"""

    def setUp(self):
        self.client = Client()
        self.program = Program.objects.create(name="Test Program")
        feat, _ = ProgramFeature.objects.get_or_create(
            key="attendance", defaults={"name": "Attendance"}
        )
        self.program.features.add(feat)
        self.kiosk_config = KioskConfig.objects.create(
            label="Main Kiosk",
            program=self.program,
        )

    def test_kiosk_page_is_public(self):
        """The kiosk page must be accessible without logging in."""
        url = reverse("kiosk_signin", args=[self.kiosk_config.pk])
        response = self.client.get(url)
        # Should not redirect to login
        self.assertNotEqual(response.status_code, 302)
        self.assertEqual(response.status_code, 200)

    def test_kiosk_page_no_api_key_in_response(self):
        """The kiosk page must NOT embed any API key in the response."""
        url = reverse("kiosk_signin", args=[self.kiosk_config.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        # Ensure no API_KEY variable is embedded
        self.assertNotContains(response, "X-API-KEY")

    def test_kiosk_page_contains_program_id(self):
        """The kiosk page should embed the program_id for proxy calls."""
        url = reverse("kiosk_signin", args=[self.kiosk_config.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, str(self.program.pk))

    def test_inactive_kiosk_not_accessible(self):
        """An inactive kiosk config should not be accessible (redirects or 404)."""
        self.kiosk_config.is_active = False
        self.kiosk_config.save()
        url = reverse("kiosk_signin", args=[self.kiosk_config.pk])
        response = self.client.get(url)
        # The custom 404 handler redirects rather than returning a 404 page
        self.assertIn(response.status_code, [302, 404])

    def test_nonexistent_kiosk_not_accessible(self):
        """A kiosk config that doesn't exist should not be accessible."""
        url = reverse("kiosk_signin", args=[99999])
        response = self.client.get(url)
        # The custom 404 handler redirects rather than returning a 404 page
        self.assertIn(response.status_code, [302, 404])

    def test_locked_kiosk_shows_unlock_form(self):
        """When the kiosk is not unlocked, the unlock form should be shown."""
        url = reverse("kiosk_signin", args=[self.kiosk_config.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Unlock Kiosk", content)

    def test_unlocked_kiosk_shows_signin_ui(self):
        """When the kiosk cookie is set, the sign-in UI should be shown."""
        url = reverse("kiosk_signin", args=[self.kiosk_config.pk])
        cookie_name = f"kiosk_unlocked_{self.kiosk_config.pk}"
        self.client.cookies[cookie_name] = "1"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("autofocus", content)

    def test_kiosk_page_has_guest_section_when_unlocked(self):
        """The kiosk page should have a section for guest/visitor sign-in when unlocked."""
        url = reverse("kiosk_signin", args=[self.kiosk_config.pk])
        cookie_name = f"kiosk_unlocked_{self.kiosk_config.pk}"
        self.client.cookies[cookie_name] = "1"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertTrue(
            "visitor" in content.lower() or "guest" in content.lower(),
            "Kiosk page should have a guest/visitor section",
        )


class KioskUnlockEndpointTests(TestCase):
    """Tests for POST /kiosk/<id>/unlock/ and POST /kiosk/<id>/lock/"""

    def setUp(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        self.client = Client()
        self.program = Program.objects.create(name="Test Program")
        feat, _ = ProgramFeature.objects.get_or_create(
            key="attendance", defaults={"name": "Attendance"}
        )
        self.program.features.add(feat)
        self.kiosk_config = KioskConfig.objects.create(
            label="Unlock Test Kiosk",
            program=self.program,
        )
        self.user = User.objects.create_user(
            username="mentor@example.com",
            email="mentor@example.com",
            password="TestPass123!",  # nosec B106
        )

    def test_unlock_with_valid_credentials_sets_cookie(self):
        """POST /kiosk/<id>/unlock/ with valid OTP code sets the unlock cookie."""
        # Mock a code in cache
        cache_key = f"kiosk_otp_{self.kiosk_config.pk}_mentor@example.com"
        cache.set(cache_key, "654321", 600)
        
        url = reverse("api_kiosk_unlock", args=[self.kiosk_config.pk])
        response = self.client.post(
            url,
            data=json.dumps(
                {"email": "mentor@example.com", "code": "654321"}
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("success"))
        cookie_name = f"kiosk_unlocked_{self.kiosk_config.pk}"
        self.assertIn(cookie_name, response.cookies)

    def test_unlock_with_invalid_credentials_returns_error(self):
        """POST /kiosk/<id>/unlock/ with bad code returns error."""
        url = reverse("api_kiosk_unlock", args=[self.kiosk_config.pk])
        response = self.client.post(
            url,
            data=json.dumps(
                {"email": "mentor@example.com", "code": "WrongCode"}
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)
        data = response.json()
        self.assertFalse(data.get("success"))

    def test_lock_clears_cookie(self):
        """POST /kiosk/<id>/lock/ clears the unlock cookie."""
        cookie_name = f"kiosk_unlocked_{self.kiosk_config.pk}"
        self.client.cookies[cookie_name] = "1"
        url = reverse("api_kiosk_lock", args=[self.kiosk_config.pk])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("success"))
        # Cookie should be deleted (max_age=0 or empty value)
        if cookie_name in response.cookies:
            self.assertEqual(response.cookies[cookie_name].value, "")


class KioskIndexTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.program = Program.objects.create(name="Test Program")
        feat, _ = ProgramFeature.objects.get_or_create(
            key="attendance", defaults={"name": "Attendance"}
        )
        self.program.features.add(feat)

    def test_kiosk_index_lists_active_kiosks(self):
        KioskConfig.objects.create(
            label="Active Kiosk",
            program=self.program,
            is_active=True,
        )
        response = self.client.get(reverse("kiosk_index"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Active Kiosk")

    def test_kiosk_index_excludes_inactive_kiosks(self):
        KioskConfig.objects.create(
            label="Inactive Kiosk",
            program=self.program,
            is_active=False,
        )
        response = self.client.get(reverse("kiosk_index"))
        self.assertNotContains(response, "Inactive Kiosk")

    def test_kiosk_index_excludes_programs_without_attendance(self):
        other_program = Program.objects.create(name="No Attendance Program")
        KioskConfig.objects.create(
            label="Ghost Kiosk",
            program=other_program,
            is_active=True,
        )
        response = self.client.get(reverse("kiosk_index"))
        self.assertNotContains(response, "Ghost Kiosk")


class KioskOTPUntockTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.program = Program.objects.create(name="OTP Program")
        feat, _ = ProgramFeature.objects.get_or_create(
            key="attendance", defaults={"name": "Attendance"}
        )
        self.program.features.add(feat)
        self.kiosk = KioskConfig.objects.create(
            label="OTP Kiosk",
            program=self.program,
        )
        self.mentor = Adult.objects.create(
            first_name="Mentor",
            last_name="Joe",
            andrew_email="mentor@andrew.cmu.edu",
            is_mentor=True,
        )

    def test_kiosk_request_code_success(self):
        url = reverse("api_kiosk_request_code", args=[self.kiosk.id])
        response = self.client.post(
            url,
            data=json.dumps({"email": "mentor@andrew.cmu.edu"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])

        # Check if code is in cache
        cache_key = f"kiosk_otp_{self.kiosk.id}_mentor@andrew.cmu.edu"
        self.assertIsNotNone(cache.get(cache_key))

    def test_kiosk_request_code_unauthorized_email(self):
        url = reverse("api_kiosk_request_code", args=[self.kiosk.id])
        response = self.client.post(
            url,
            data=json.dumps({"email": "unknown@example.com"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.json()["success"])

    def test_kiosk_unlock_success(self):
        # First request a code
        cache_key = f"kiosk_otp_{self.kiosk.id}_mentor@andrew.cmu.edu"
        cache.set(cache_key, "123456", 600)

        url = reverse("api_kiosk_unlock", args=[self.kiosk.id])
        response = self.client.post(
            url,
            data=json.dumps({"email": "mentor@andrew.cmu.edu", "code": "123456"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])

        # Check if cookie is set
        cookie_name = f"kiosk_unlocked_{self.kiosk.id}"
        self.assertEqual(response.cookies[cookie_name].value, "1")

        # Cache should be cleared
        self.assertIsNone(cache.get(cache_key))

    def test_kiosk_unlock_invalid_code(self):
        cache_key = f"kiosk_otp_{self.kiosk.id}_mentor@andrew.cmu.edu"
        cache.set(cache_key, "123456", 600)

        url = reverse("api_kiosk_unlock", args=[self.kiosk.id])
        response = self.client.post(
            url,
            data=json.dumps({"email": "mentor@andrew.cmu.edu", "code": "wrong"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.json()["success"])


class KioskProxyTapTests(TestCase):
    """Tests for POST /kiosk/<id>/tap/ proxy endpoint."""

    def setUp(self):
        self.client = Client()
        self.program = Program.objects.create(name="Test Program")
        feat, _ = ProgramFeature.objects.get_or_create(
            key="attendance", defaults={"name": "Attendance"}
        )
        self.program.features.add(feat)
        self.kiosk_config = KioskConfig.objects.create(
            label="Tap Test Kiosk",
            program=self.program,
        )
        self.cookie_name = f"kiosk_unlocked_{self.kiosk_config.pk}"

    def test_tap_without_cookie_returns_403(self):
        """POST /kiosk/<id>/tap/ without unlock cookie returns 403."""
        url = reverse("api_kiosk_tap", args=[self.kiosk_config.pk])
        response = self.client.post(
            url,
            data=json.dumps({"visitor_name": "Test Visitor"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)

    def test_tap_with_cookie_records_attendance(self):
        """POST /kiosk/<id>/tap/ with unlock cookie records attendance."""
        self.client.cookies[self.cookie_name] = "1"
        url = reverse("api_kiosk_tap", args=[self.kiosk_config.pk])
        response = self.client.post(
            url,
            data=json.dumps({"visitor_name": "Test Visitor"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("event_type", data)

    def test_tap_with_cookie_returns_event_type(self):
        """Tap response includes event_type (IN or OUT)."""
        self.client.cookies[self.cookie_name] = "1"
        url = reverse("api_kiosk_tap", args=[self.kiosk_config.pk])
        response = self.client.post(
            url,
            data=json.dumps({"visitor_name": "Jane Doe"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn(data["event_type"], ["IN", "OUT"])


class KioskProxyLookupTests(TestCase):
    """Tests for GET /kiosk/<id>/lookup/ proxy endpoint."""

    def setUp(self):
        self.client = Client()
        self.program = Program.objects.create(name="Test Program")
        feat, _ = ProgramFeature.objects.get_or_create(
            key="attendance", defaults={"name": "Attendance"}
        )
        self.program.features.add(feat)
        self.kiosk_config = KioskConfig.objects.create(
            label="Lookup Test Kiosk",
            program=self.program,
        )
        self.cookie_name = f"kiosk_unlocked_{self.kiosk_config.pk}"
        self.student = Student.objects.create(
            legal_first_name="Bob",
            first_name="Bob",
            last_name="Jones",
        )
        self.rfid = RFIDCard.objects.create(uid="RFID999", student=self.student)

    def test_lookup_without_cookie_returns_403(self):
        """GET /kiosk/<id>/lookup/ without unlock cookie returns 403."""
        url = reverse("api_kiosk_lookup", args=[self.kiosk_config.pk])
        response = self.client.get(url, {"name": "Bob"})
        self.assertEqual(response.status_code, 403)

    def test_lookup_with_cookie_by_name(self):
        """GET /kiosk/<id>/lookup/ with cookie returns matching students."""
        self.client.cookies[self.cookie_name] = "1"
        url = reverse("api_kiosk_lookup", args=[self.kiosk_config.pk])
        response = self.client.get(url, {"name": "Bob"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("students", data)
        self.assertGreaterEqual(len(data["students"]), 1)

    def test_lookup_with_cookie_by_rfid(self):
        """GET /kiosk/<id>/lookup/ with cookie returns student by RFID."""
        self.client.cookies[self.cookie_name] = "1"
        url = reverse("api_kiosk_lookup", args=[self.kiosk_config.pk])
        response = self.client.get(url, {"rfid": "RFID999"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("students", data)
        self.assertEqual(len(data["students"]), 1)
        self.assertEqual(data["students"][0]["id"], self.student.id)

    def test_lookup_no_params_returns_empty(self):
        """GET /kiosk/<id>/lookup/ with no params returns empty list."""
        self.client.cookies[self.cookie_name] = "1"
        url = reverse("api_kiosk_lookup", args=[self.kiosk_config.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["students"], [])


class KioskConfigSettingsTests(TestCase):
    """Tests for KioskConfig management via the portal settings page."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Group

        User = get_user_model()
        self.user = User.objects.create_user(
            username="leadmentor@example.com",
            email="leadmentor@example.com",
            password="TestPass123!",  # nosec B106
        )
        self.user.is_superuser = True
        self.user.is_staff = True
        self.user.save()
        group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.user.groups.add(group)

        self.program = Program.objects.create(name="Test Program")
        feat, _ = ProgramFeature.objects.get_or_create(
            key="attendance", defaults={"name": "Attendance"}
        )
        self.program.features.add(feat)
        self.client = Client()
        self.client.force_login(self.user)

    def test_kiosk_configs_shown_in_settings(self):
        """The settings page should show a kiosk configurations tab/section."""
        response = self.client.get("/programs/settings/?tab=kiosk_configs")
        self.assertEqual(response.status_code, 200)

    def test_add_kiosk_config(self):
        """Staff can add a new kiosk config via the settings page (no api_key needed)."""
        response = self.client.post(
            "/programs/settings/",
            {
                "action": "add_kiosk_config",
                "label": "New Kiosk",
                "program_id": self.program.pk,
            },
        )
        # Should redirect back to the settings page
        self.assertEqual(response.status_code, 302)
        self.assertTrue(KioskConfig.objects.filter(label="New Kiosk").exists())

    def test_delete_kiosk_config(self):
        """Staff can delete a kiosk config via the settings page."""
        config = KioskConfig.objects.create(
            label="Delete Me",
            program=self.program,
        )
        response = self.client.post(
            "/programs/settings/",
            {
                "action": "delete_kiosk_config",
                "kiosk_config_id": config.pk,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(KioskConfig.objects.filter(pk=config.pk).exists())

    def test_toggle_kiosk_config_active(self):
        """Staff can toggle a kiosk config active/inactive."""
        config = KioskConfig.objects.create(
            label="Toggle Me",
            program=self.program,
            is_active=True,
        )
        response = self.client.post(
            "/programs/settings/",
            {
                "action": "toggle_kiosk_config",
                "kiosk_config_id": config.pk,
            },
        )
        self.assertEqual(response.status_code, 302)
        config.refresh_from_db()
        self.assertFalse(config.is_active)
