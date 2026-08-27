"""Kiosk tests: config model, page views, unlock endpoint, OTP unlock, proxy tap/lookup."""

import json

from django.contrib.auth.models import Group
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse

from attendance.models import KioskConfig, RFIDCard
from programs.models import Adult, Program, ProgramFeature, Student

from .base import make_client, make_program


class KioskConfigModelTests(TestCase):
    def setUp(self):
        self.program = make_program()

    def test_kiosk_config_str(self):
        config = KioskConfig.objects.create(
            label="Build Space Kiosk", program=self.program
        )
        self.assertIn("Build Space Kiosk", str(config))

    def test_kiosk_config_defaults(self):
        config = KioskConfig.objects.create(label="Kiosk", program=self.program)
        self.assertTrue(config.is_active)

    def test_kiosk_config_requires_label_and_program(self):
        with self.assertRaises(Exception):
            KioskConfig.objects.create(label="X")


class KioskPageViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.program = make_program()
        self.kiosk_config = KioskConfig.objects.create(
            label="Main Kiosk", program=self.program
        )

    def test_kiosk_page_is_public(self):
        url = reverse("kiosk_signin", args=[self.kiosk_config.pk])
        response = self.client.get(url)
        self.assertNotEqual(response.status_code, 302)
        self.assertEqual(response.status_code, 200)

    def test_kiosk_page_no_api_key_in_response(self):
        url = reverse("kiosk_signin", args=[self.kiosk_config.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "X-API-KEY")

    def test_kiosk_page_contains_program_id(self):
        url = reverse("kiosk_signin", args=[self.kiosk_config.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, str(self.program.pk))

    def test_inactive_kiosk_not_accessible(self):
        self.kiosk_config.is_active = False
        self.kiosk_config.save()
        url = reverse("kiosk_signin", args=[self.kiosk_config.pk])
        response = self.client.get(url)
        self.assertIn(response.status_code, [302, 404])

    def test_nonexistent_kiosk_not_accessible(self):
        url = reverse("kiosk_signin", args=[99999])
        response = self.client.get(url)
        self.assertIn(response.status_code, [302, 404])

    def test_locked_kiosk_shows_unlock_form(self):
        url = reverse("kiosk_signin", args=[self.kiosk_config.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Unlock Kiosk", content)

    def test_unlocked_kiosk_shows_signin_ui(self):
        url = reverse("kiosk_signin", args=[self.kiosk_config.pk])
        cookie_name = f"kiosk_unlocked_{self.kiosk_config.pk}"
        self.client.cookies[cookie_name] = "1"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("autofocus", content)

    def test_kiosk_page_has_guest_section_when_unlocked(self):
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

    def test_kiosk_toast_container_positioned_at_bottom(self):
        url = reverse("kiosk_signin", args=[self.kiosk_config.pk])
        cookie_name = f"kiosk_unlocked_{self.kiosk_config.pk}"
        self.client.cookies[cookie_name] = "1"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertNotIn("position-fixed top-0 start-50", content)
        self.assertIn("position-fixed bottom-0", content)

    def test_kiosk_page_reminds_students_to_use_member_tab(self):
        url = reverse("kiosk_signin", args=[self.kiosk_config.pk])
        cookie_name = f"kiosk_unlocked_{self.kiosk_config.pk}"
        self.client.cookies[cookie_name] = "1"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Girls of Steel student", content)
        self.assertIn("Member", content)

    def test_kiosk_page_switches_back_to_member_tab_after_guest_signin(self):
        url = reverse("kiosk_signin", args=[self.kiosk_config.pk])
        cookie_name = f"kiosk_unlocked_{self.kiosk_config.pk}"
        self.client.cookies[cookie_name] = "1"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("member-tab", content)
        self.assertIn("showMemberTab", content)

    def test_kiosk_enter_key_wired_to_member_signin(self):
        url = reverse("kiosk_signin", args=[self.kiosk_config.pk])
        cookie_name = f"kiosk_unlocked_{self.kiosk_config.pk}"
        self.client.cookies[cookie_name] = "1"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn(
            'memberInput.addEventListener("keydown"',
            content,
            "Pressing Enter in the member name field should trigger sign-in",
        )

    def test_kiosk_enter_key_wired_to_guest_signin(self):
        url = reverse("kiosk_signin", args=[self.kiosk_config.pk])
        cookie_name = f"kiosk_unlocked_{self.kiosk_config.pk}"
        self.client.cookies[cookie_name] = "1"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn(
            'guestInput.addEventListener("keydown"',
            content,
            "Pressing Enter in the guest name field should trigger sign-in",
        )

    def test_kiosk_enter_key_wired_to_guest_team_number_signin(self):
        url = reverse("kiosk_signin", args=[self.kiosk_config.pk])
        cookie_name = f"kiosk_unlocked_{self.kiosk_config.pk}"
        self.client.cookies[cookie_name] = "1"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn(
            'guestTeamInput.addEventListener("keydown"',
            content,
            "Pressing Enter in the guest team number field should trigger sign-in",
        )


class KioskUnlockEndpointTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        self.client = Client()
        self.program = make_program()
        self.kiosk_config = KioskConfig.objects.create(
            label="Unlock Test Kiosk", program=self.program
        )
        self.user = User.objects.create_user(
            username="mentor@example.com",
            email="mentor@example.com",
            password="TestPass123!",  # nosec B106
        )

    def test_unlock_with_valid_credentials_sets_cookie(self):
        cache_key = f"kiosk_otp_{self.kiosk_config.pk}_mentor@example.com"
        cache.set(cache_key, "654321", 600)
        url = reverse("api_kiosk_unlock", args=[self.kiosk_config.pk])
        response = self.client.post(
            url,
            data=json.dumps({"email": "mentor@example.com", "code": "654321"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("success"))
        cookie_name = f"kiosk_unlocked_{self.kiosk_config.pk}"
        self.assertIn(cookie_name, response.cookies)

    def test_unlock_with_invalid_credentials_returns_error(self):
        url = reverse("api_kiosk_unlock", args=[self.kiosk_config.pk])
        response = self.client.post(
            url,
            data=json.dumps({"email": "mentor@example.com", "code": "WrongCode"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)
        data = response.json()
        self.assertFalse(data.get("success"))

    def test_lock_clears_cookie(self):
        cookie_name = f"kiosk_unlocked_{self.kiosk_config.pk}"
        self.client.cookies[cookie_name] = "1"
        url = reverse("api_kiosk_lock", args=[self.kiosk_config.pk])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("success"))
        if cookie_name in response.cookies:
            self.assertEqual(response.cookies[cookie_name].value, "")


class KioskIndexTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.program = make_program()

    def test_kiosk_index_lists_active_kiosks(self):
        KioskConfig.objects.create(
            label="Active Kiosk", program=self.program, is_active=True
        )
        response = self.client.get(reverse("kiosk_index"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Active Kiosk")

    def test_kiosk_index_excludes_inactive_kiosks(self):
        KioskConfig.objects.create(
            label="Inactive Kiosk", program=self.program, is_active=False
        )
        response = self.client.get(reverse("kiosk_index"))
        self.assertNotContains(response, "Inactive Kiosk")

    def test_kiosk_index_excludes_programs_without_attendance(self):
        from programs.models import Program

        other_program = Program.objects.create(name="No Attendance Program")
        KioskConfig.objects.create(
            label="Ghost Kiosk", program=other_program, is_active=True
        )
        response = self.client.get(reverse("kiosk_index"))
        self.assertNotContains(response, "Ghost Kiosk")


class KioskOTPUntockTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.program = make_program("OTP Program")
        self.kiosk = KioskConfig.objects.create(label="OTP Kiosk", program=self.program)
        self.mentor = Adult.objects.create(
            legal_first_name="Mentor",
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
        cookie_name = f"kiosk_unlocked_{self.kiosk.id}"
        self.assertEqual(response.cookies[cookie_name].value, "1")
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
    def setUp(self):
        self.client = Client()
        self.program = make_program()
        self.kiosk_config = KioskConfig.objects.create(
            label="Tap Test Kiosk", program=self.program
        )
        self.cookie_name = f"kiosk_unlocked_{self.kiosk_config.pk}"

    def test_tap_without_cookie_returns_403(self):
        url = reverse("api_kiosk_tap", args=[self.kiosk_config.pk])
        response = self.client.post(
            url,
            data=json.dumps({"visitor_name": "Test Visitor"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)

    def test_tap_with_cookie_records_attendance(self):
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
    def setUp(self):
        self.client = Client()
        self.program = make_program()
        self.kiosk_config = KioskConfig.objects.create(
            label="Lookup Test Kiosk", program=self.program
        )
        self.cookie_name = f"kiosk_unlocked_{self.kiosk_config.pk}"
        self.student = Student.objects.create(legal_first_name="Bob", last_name="Jones")
        self.rfid = RFIDCard.objects.create(uid="RFID999", student=self.student)

    def test_lookup_without_cookie_returns_403(self):
        url = reverse("api_kiosk_lookup", args=[self.kiosk_config.pk])
        response = self.client.get(url, {"name": "Bob"})
        self.assertEqual(response.status_code, 403)

    def test_lookup_with_cookie_by_name(self):
        self.client.cookies[self.cookie_name] = "1"
        url = reverse("api_kiosk_lookup", args=[self.kiosk_config.pk])
        response = self.client.get(url, {"name": "Bob"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("students", data)
        self.assertGreaterEqual(len(data["students"]), 1)

    def test_lookup_with_cookie_by_rfid(self):
        self.client.cookies[self.cookie_name] = "1"
        url = reverse("api_kiosk_lookup", args=[self.kiosk_config.pk])
        response = self.client.get(url, {"rfid": "RFID999"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("students", data)
        self.assertEqual(len(data["students"]), 1)
        self.assertEqual(data["students"][0]["id"], self.student.id)

    def test_lookup_no_params_returns_empty(self):
        self.client.cookies[self.cookie_name] = "1"
        url = reverse("api_kiosk_lookup", args=[self.kiosk_config.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["students"], [])

    def test_lookup_by_student_preferred_name(self):
        student = Student.objects.create(
            legal_first_name="Barbara", preferred_first_name="Babs", last_name="Smith"
        )
        self.client.cookies[self.cookie_name] = "1"
        url = reverse("api_kiosk_lookup", args=[self.kiosk_config.pk])
        response = self.client.get(url, {"name": "Babs Smith"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        ids = [s["id"] for s in data["students"]]
        self.assertIn(student.id, ids)

    def test_lookup_by_student_legal_name(self):
        student = Student.objects.create(
            legal_first_name="Barbara", preferred_first_name="Babs", last_name="Smith"
        )
        self.client.cookies[self.cookie_name] = "1"
        url = reverse("api_kiosk_lookup", args=[self.kiosk_config.pk])
        response = self.client.get(url, {"name": "Barbara Smith"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        ids = [s["id"] for s in data["students"]]
        self.assertIn(student.id, ids)

    def test_lookup_by_mentor_preferred_name(self):
        mentor = Adult.objects.create(
            legal_first_name="Robert",
            preferred_first_name="Bobby",
            last_name="Martin",
            is_mentor=True,
        )
        self.client.cookies[self.cookie_name] = "1"
        url = reverse("api_kiosk_lookup", args=[self.kiosk_config.pk])
        response = self.client.get(url, {"name": "Bobby Martin"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        ids = [s["id"] for s in data["students"]]
        self.assertIn(mentor.id, ids)

    def test_lookup_by_mentor_legal_name(self):
        mentor = Adult.objects.create(
            preferred_first_name="Robert",
            legal_first_name="Rob",
            last_name="Martin",
            is_mentor=True,
        )
        self.client.cookies[self.cookie_name] = "1"
        url = reverse("api_kiosk_lookup", args=[self.kiosk_config.pk])
        response = self.client.get(url, {"name": "Robert Martin"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        ids = [s["id"] for s in data["students"]]
        self.assertIn(mentor.id, ids)


class KioskConfigSettingsTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model

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
        self.program = make_program()
        self.client = Client()
        self.client.force_login(self.user)

    def test_kiosk_configs_shown_in_settings(self):
        response = self.client.get("/programs/settings/?tab=kiosk_configs")
        self.assertEqual(response.status_code, 200)

    def test_add_kiosk_config(self):
        response = self.client.post(
            reverse("portal_kiosk"),
            {
                "action": "add_kiosk_config",
                "label": "New Kiosk",
                "program_id": self.program.pk,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(KioskConfig.objects.filter(label="New Kiosk").exists())

    def test_delete_kiosk_config(self):
        config = KioskConfig.objects.create(label="Delete Me", program=self.program)
        response = self.client.post(
            reverse("portal_kiosk"),
            {
                "action": "delete_kiosk_config",
                "kiosk_config_id": config.pk,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(KioskConfig.objects.filter(pk=config.pk).exists())

    def test_toggle_kiosk_config_active(self):
        config = KioskConfig.objects.create(
            label="Toggle Me", program=self.program, is_active=True
        )
        response = self.client.post(
            reverse("portal_kiosk"),
            {
                "action": "toggle_kiosk_config",
                "kiosk_config_id": config.pk,
            },
        )
        self.assertEqual(response.status_code, 302)
        config.refresh_from_db()
        self.assertFalse(config.is_active)


class KioskFirstNameTests(TestCase):
    """Kiosk API tap/lookup responses should return first-name-only for
    students and mentors, but the who-is-here screen returns full names for
    everyone (in case of emergencies)."""

    def setUp(self):
        self.client = Client()
        self.program = make_program()
        self.kiosk_config = KioskConfig.objects.create(
            label="Name Test Kiosk", program=self.program
        )
        self.cookie_name = f"kiosk_unlocked_{self.kiosk_config.pk}"

        self.student = Student.objects.create(
            legal_first_name="Ada",
            preferred_first_name="Ada",
            last_name="Lovelace",
        )
        self.mentor = Adult.objects.create(
            legal_first_name="Robert",
            preferred_first_name="Bobby",
            last_name="Martin",
            is_mentor=True,
        )
        self.rfid_student = RFIDCard.objects.create(
            uid="RFID-STU-001", student=self.student
        )

    # ── tap endpoint ───────────────────────────────────────────────────

    def test_tap_student_returns_first_name(self):
        self.client.cookies[self.cookie_name] = "1"
        url = reverse("api_kiosk_tap", args=[self.kiosk_config.pk])
        response = self.client.post(
            url,
            data=json.dumps({"student_id": self.student.pk}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["student"], "Ada")

    def test_tap_student_uses_first_name_over_legal(self):
        self.student.preferred_first_name = "Addy"
        self.student.save()
        self.client.cookies[self.cookie_name] = "1"
        url = reverse("api_kiosk_tap", args=[self.kiosk_config.pk])
        response = self.client.post(
            url,
            data=json.dumps({"student_id": self.student.pk}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["student"], "Addy")

    def test_tap_student_without_first_name_uses_legal(self):
        self.student.preferred_first_name = None
        self.student.save()
        self.client.cookies[self.cookie_name] = "1"
        url = reverse("api_kiosk_tap", args=[self.kiosk_config.pk])
        response = self.client.post(
            url,
            data=json.dumps({"student_id": self.student.pk}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["student"], "Ada")

    def test_tap_student_by_rfid_returns_first_name(self):
        self.client.cookies[self.cookie_name] = "1"
        url = reverse("api_kiosk_tap", args=[self.kiosk_config.pk])
        response = self.client.post(
            url,
            data=json.dumps({"rfid_uid": "RFID-STU-001"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["student"], "Ada")

    def test_tap_mentor_returns_preferred_first_name(self):
        self.client.cookies[self.cookie_name] = "1"
        url = reverse("api_kiosk_tap", args=[self.kiosk_config.pk])
        response = self.client.post(
            url,
            data=json.dumps({"adult_id": self.mentor.pk}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["student"], "Bobby")

    def test_tap_mentor_without_preferred_uses_first_name(self):
        self.mentor.preferred_first_name = None
        self.mentor.save()
        self.client.cookies[self.cookie_name] = "1"
        url = reverse("api_kiosk_tap", args=[self.kiosk_config.pk])
        response = self.client.post(
            url,
            data=json.dumps({"adult_id": self.mentor.pk}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["student"], "Robert")

    def test_tap_visitor_returns_full_name(self):
        self.client.cookies[self.cookie_name] = "1"
        url = reverse("api_kiosk_tap", args=[self.kiosk_config.pk])
        response = self.client.post(
            url,
            data=json.dumps({"visitor_name": "Jane Doe"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["student"], "Jane Doe")

    # ── lookup endpoint ────────────────────────────────────────────────

    def test_lookup_student_returns_first_name(self):
        self.client.cookies[self.cookie_name] = "1"
        url = reverse("api_kiosk_lookup", args=[self.kiosk_config.pk])
        response = self.client.get(url, {"name": "Ada"})
        data = response.json()
        names = [s["name"] for s in data["students"]]
        self.assertIn("Ada", names)
        self.assertNotIn("Ada Lovelace", names)

    def test_lookup_student_by_rfid_returns_first_name(self):
        self.client.cookies[self.cookie_name] = "1"
        url = reverse("api_kiosk_lookup", args=[self.kiosk_config.pk])
        response = self.client.get(url, {"rfid": "RFID-STU-001"})
        data = response.json()
        self.assertEqual(data["students"][0]["name"], "Ada")

    def test_lookup_mentor_returns_preferred_first_name(self):
        self.client.cookies[self.cookie_name] = "1"
        url = reverse("api_kiosk_lookup", args=[self.kiosk_config.pk])
        response = self.client.get(url, {"name": "Bobby"})
        data = response.json()
        names = [s["name"] for s in data["students"]]
        self.assertIn("Bobby", names)
        self.assertNotIn("Bobby Martin", names)

    def test_lookup_mentor_without_preferred_returns_first_name(self):
        self.mentor.preferred_first_name = None
        self.mentor.save()
        self.client.cookies[self.cookie_name] = "1"
        url = reverse("api_kiosk_lookup", args=[self.kiosk_config.pk])
        response = self.client.get(url, {"name": "Robert"})
        data = response.json()
        names = [s["name"] for s in data["students"]]
        self.assertIn("Robert", names)

    # ── who-is-here endpoint ───────────────────────────────────────────

    def test_who_is_here_student_returns_full_name(self):
        self.client.cookies[self.cookie_name] = "1"
        tap_url = reverse("api_kiosk_tap", args=[self.kiosk_config.pk])
        self.client.post(
            tap_url,
            data=json.dumps({"student_id": self.student.pk}),
            content_type="application/json",
        )
        url = reverse("api_kiosk_who_is_here", args=[self.kiosk_config.pk])
        response = self.client.get(url)
        data = response.json()
        names = [p["name"] for p in data["people"]]
        self.assertIn("Ada Lovelace", names)

    def test_who_is_here_mentor_returns_full_name(self):
        self.client.cookies[self.cookie_name] = "1"
        tap_url = reverse("api_kiosk_tap", args=[self.kiosk_config.pk])
        self.client.post(
            tap_url,
            data=json.dumps({"adult_id": self.mentor.pk}),
            content_type="application/json",
        )
        url = reverse("api_kiosk_who_is_here", args=[self.kiosk_config.pk])
        response = self.client.get(url)
        data = response.json()
        names = [p["name"] for p in data["people"]]
        self.assertIn("Bobby Martin", names)

    def test_who_is_here_visitor_returns_full_name(self):
        self.client.cookies[self.cookie_name] = "1"
        tap_url = reverse("api_kiosk_tap", args=[self.kiosk_config.pk])
        self.client.post(
            tap_url,
            data=json.dumps({"visitor_name": "Jane Doe"}),
            content_type="application/json",
        )
        url = reverse("api_kiosk_who_is_here", args=[self.kiosk_config.pk])
        response = self.client.get(url)
        data = response.json()
        names = [p["name"] for p in data["people"]]
        self.assertIn("Jane Doe", names)
