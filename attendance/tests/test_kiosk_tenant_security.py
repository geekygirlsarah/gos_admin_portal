import json
import secrets
import time
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import signing
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from attendance.kiosk_utils import (
    _COOKIE_MAX_AGE,
    _otp_cache_key,
    _otp_ready_cache_key,
)
from attendance.models import (
    AttendanceEvent,
    AttendanceSession,
    KioskConfig,
    RFIDCard,
)
from attendance.services import record_tap
from organizations.models import Organization, UserOrganizationMembership
from programs.models import Adult, Enrollment, Program, ProgramFeature, Student


@override_settings(
    ALLOWED_HOSTS=[".momentumteam.app", "testserver"],
    MOMENTUM_ROOT_DOMAIN="momentumteam.app",
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "kiosk-tenant-security-tests",
        }
    },
)
class KioskTenantSecurityTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()
        self.alpha = Organization.objects.create(
            name="Alpha", slug="kiosk-alpha", subdomain="kiosk-alpha"
        )
        self.beta = Organization.objects.create(
            name="Beta", slug="kiosk-beta", subdomain="kiosk-beta"
        )
        self.alpha_host = "kiosk-alpha.momentumteam.app"
        self.beta_host = "kiosk-beta.momentumteam.app"
        self.alpha_program = self.make_program(self.alpha, "Alpha Program")
        self.beta_program = self.make_program(self.beta, "Beta Program")
        self.alpha_kiosk = KioskConfig.objects.create(
            label="Alpha Kiosk", organization=self.alpha, program=self.alpha_program
        )
        self.beta_kiosk = KioskConfig.objects.create(
            label="Beta Kiosk", organization=self.beta, program=self.beta_program
        )
        self.mentor_user = User.objects.create_user(
            username="alpha-mentor",
            email="mentor@alpha.example",
        )
        self.mentor = Adult.objects.create(
            organization=self.alpha,
            user=self.mentor_user,
            legal_first_name="Ada",
            last_name="Mentor",
            is_mentor=True,
            mentor_active=True,
            login_enabled=True,
        )
        self.membership, _ = UserOrganizationMembership.objects.get_or_create(
            user=self.mentor_user,
            organization=self.alpha,
            role="Mentor",
            defaults={"is_active": True},
        )
        self.student = Student.objects.create(
            organization=self.alpha,
            legal_first_name="Alpha",
            last_name="Student",
        )
        Enrollment.objects.create(
            organization=self.alpha,
            student=self.student,
            program=self.alpha_program,
            active=True,
        )

    @staticmethod
    def make_program(organization, name):
        attendance, _ = ProgramFeature.objects.get_or_create(
            organization=organization,
            key="attendance",
            defaults={"name": "Attendance"},
        )
        program = Program.objects.create(organization=organization, name=name)
        program.features.add(attendance)
        return program

    def set_unlocked(self, kiosk, value=None):
        value = value or signing.dumps(
            {
                "kiosk_id": kiosk.pk,
                "organization_id": kiosk.organization_id,
            },
            salt="attendance.kiosk_unlock",
        )
        self.client.cookies[f"kiosk_unlocked_{kiosk.pk}"] = value

    def post_json(self, url, data, host=None):
        return self.client.post(
            url,
            data=json.dumps(data),
            content_type="application/json",
            HTTP_HOST=host or self.alpha_host,
        )

    def test_kiosk_index_lists_only_current_organization(self):
        response = self.client.get(reverse("kiosk_index"), HTTP_HOST=self.alpha_host)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Alpha Kiosk")
        self.assertNotContains(response, "Beta Kiosk")

    def test_foreign_kiosk_id_is_not_available_on_current_tenant_host(self):
        response = self.client.get(
            reverse("kiosk_signin", args=[self.beta_kiosk.pk]),
            HTTP_HOST=self.alpha_host,
        )
        self.assertIn(response.status_code, (302, 404))
        self.assertNotIn(b"Beta Kiosk", response.content)

    def test_kiosk_with_program_from_another_organization_is_rejected(self):
        mismatched = KioskConfig.objects.create(
            label="Mismatched Kiosk",
            organization=self.alpha,
            program=self.beta_program,
        )
        response = self.client.get(
            reverse("kiosk_signin", args=[mismatched.pk]),
            HTTP_HOST=self.alpha_host,
        )
        self.assertIn(response.status_code, (302, 404))
        self.assertNotIn(b"Mismatched Kiosk", response.content)

    def test_kiosk_without_request_organization_fails_closed(self):
        from attendance.kiosk_views import kiosk_signin

        request = RequestFactory().get("/")
        request.organization = None
        with self.assertRaises(Http404):
            kiosk_signin(request, self.alpha_kiosk.pk)

    def test_plain_or_tampered_unlock_cookies_are_rejected(self):
        url = reverse("api_kiosk_tap", args=[self.alpha_kiosk.pk])
        for value in ("1", "not-a-valid-signature"):
            with self.subTest(value=value):
                self.client.cookies[f"kiosk_unlocked_{self.alpha_kiosk.pk}"] = value
                response = self.post_json(url, {"visitor_name": "Test Visitor"})
                self.assertEqual(response.status_code, 403)
        self.assertFalse(AttendanceEvent.objects.exists())

    def test_signed_unlock_cookie_is_bound_to_kiosk_and_organization(self):
        url = reverse("api_kiosk_tap", args=[self.alpha_kiosk.pk])
        for claims in (
            {"kiosk_id": self.alpha_kiosk.pk, "organization_id": self.beta.pk},
            {"kiosk_id": self.beta_kiosk.pk, "organization_id": self.alpha.pk},
        ):
            with self.subTest(claims=claims):
                value = signing.dumps(claims, salt="attendance.kiosk_unlock")
                self.set_unlocked(self.alpha_kiosk, value=value)
                response = self.post_json(url, {"visitor_name": "Test Visitor"})
                self.assertEqual(response.status_code, 403)
        self.assertFalse(AttendanceEvent.objects.exists())

    def test_expired_signed_unlock_cookie_is_rejected(self):
        with patch.object(time, "time", return_value=time.time() - _COOKIE_MAX_AGE - 1):
            self.set_unlocked(self.alpha_kiosk)
        response = self.post_json(
            reverse("api_kiosk_tap", args=[self.alpha_kiosk.pk]),
            {"visitor_name": "Test Visitor"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(AttendanceEvent.objects.exists())

    @patch("allauth.account.adapter.get_adapter")
    def test_active_tenant_mentor_can_request_unlock_code(self, get_adapter):
        adapter = get_adapter.return_value
        adapter.generate_login_code.return_value = "654321"
        response = self.post_json(
            reverse("api_kiosk_request_code", args=[self.alpha_kiosk.pk]),
            {"email": self.mentor_user.email},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        adapter.send_mail.assert_called_once()

    @patch("allauth.account.adapter.get_adapter")
    def test_nonmember_email_cannot_request_code_or_create_account(self, get_adapter):
        response = self.post_json(
            reverse("api_kiosk_request_code", args=[self.alpha_kiosk.pk]),
            {"email": "new-person@example.org"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(User.objects.filter(email="new-person@example.org").exists())
        get_adapter.return_value.send_mail.assert_not_called()

    @patch("allauth.account.adapter.get_adapter")
    def test_inactive_membership_cannot_request_code(self, get_adapter):
        self.membership.is_active = False
        self.membership.save(update_fields=["is_active"])
        response = self.post_json(
            reverse("api_kiosk_request_code", args=[self.alpha_kiosk.pk]),
            {"email": self.mentor_user.email},
        )
        self.assertEqual(response.status_code, 403)
        get_adapter.return_value.send_mail.assert_not_called()

    @patch("allauth.account.adapter.get_adapter")
    def test_nonmentor_role_cannot_request_code(self, get_adapter):
        student_user = User.objects.create_user(
            username="alpha-student-account", email="student@alpha.example"
        )
        UserOrganizationMembership.objects.create(
            user=student_user,
            organization=self.alpha,
            role="Student",
            is_active=True,
        )
        response = self.post_json(
            reverse("api_kiosk_request_code", args=[self.alpha_kiosk.pk]),
            {"email": student_user.email},
        )
        self.assertEqual(response.status_code, 403)
        get_adapter.return_value.send_mail.assert_not_called()

    @patch("allauth.account.adapter.get_adapter")
    def test_lead_mentor_can_request_code_without_adult_profile(self, get_adapter):
        lead = User.objects.create_user(
            username="alpha-lead-account", email="lead@alpha.example"
        )
        UserOrganizationMembership.objects.create(
            user=lead,
            organization=self.alpha,
            role="LeadMentor",
            is_active=True,
        )
        get_adapter.return_value.generate_login_code.return_value = "654321"
        response = self.post_json(
            reverse("api_kiosk_request_code", args=[self.alpha_kiosk.pk]),
            {"email": lead.email},
        )
        self.assertEqual(response.status_code, 200)
        get_adapter.return_value.send_mail.assert_called_once()

    @patch("allauth.account.adapter.get_adapter")
    def test_active_platform_superuser_can_request_code(self, get_adapter):
        operator = User.objects.create_superuser(
            username="kiosk-platform-operator",
            email="operator@example.org",
        )
        get_adapter.return_value.generate_login_code.return_value = "654321"
        response = self.post_json(
            reverse("api_kiosk_request_code", args=[self.alpha_kiosk.pk]),
            {"email": operator.email},
        )
        self.assertEqual(response.status_code, 200)
        get_adapter.return_value.send_mail.assert_called_once()

    @patch("allauth.account.adapter.get_adapter")
    def test_inactive_platform_superuser_cannot_request_code(self, get_adapter):
        operator = User.objects.create_superuser(
            username="inactive-kiosk-operator",
            email="inactive-operator@example.org",
        )
        operator.is_active = False
        operator.save(update_fields=["is_active"])
        response = self.post_json(
            reverse("api_kiosk_request_code", args=[self.alpha_kiosk.pk]),
            {"email": operator.email},
        )
        self.assertEqual(response.status_code, 403)
        get_adapter.return_value.send_mail.assert_not_called()

    @patch("allauth.account.adapter.get_adapter")
    def test_ambiguous_email_accounts_cannot_request_code(self, get_adapter):
        User.objects.create_user(
            username="duplicate-email", email=self.mentor_user.email
        )
        response = self.post_json(
            reverse("api_kiosk_request_code", args=[self.alpha_kiosk.pk]),
            {"email": self.mentor_user.email},
        )
        self.assertEqual(response.status_code, 403)
        get_adapter.return_value.send_mail.assert_not_called()

    @patch("allauth.account.adapter.get_adapter")
    def test_code_requests_are_rate_limited(self, get_adapter):
        adapter = get_adapter.return_value
        adapter.generate_login_code.return_value = "654321"
        url = reverse("api_kiosk_request_code", args=[self.alpha_kiosk.pk])
        responses = [
            self.post_json(url, {"email": self.mentor_user.email}) for _ in range(4)
        ]
        self.assertEqual(
            [response.status_code for response in responses], [200, 200, 200, 429]
        )
        self.assertEqual(adapter.send_mail.call_count, 3)

    @patch("allauth.account.adapter.get_adapter")
    def test_requesting_a_new_code_does_not_reset_failed_attempt_limit(
        self, get_adapter
    ):
        adapter = get_adapter.return_value
        adapter.generate_login_code.return_value = "654321"
        request_url = reverse("api_kiosk_request_code", args=[self.alpha_kiosk.pk])
        self.assertEqual(
            self.post_json(request_url, {"email": self.mentor_user.email}).status_code,
            200,
        )
        unlock_url = reverse("api_kiosk_unlock", args=[self.alpha_kiosk.pk])
        for _ in range(5):
            self.post_json(
                unlock_url,
                {"email": self.mentor_user.email, "code": "000000"},
            )
        self.assertEqual(
            self.post_json(request_url, {"email": self.mentor_user.email}).status_code,
            200,
        )
        response = self.post_json(
            unlock_url,
            {"email": self.mentor_user.email, "code": "654321"},
        )
        self.assertEqual(response.status_code, 429)

    @patch.object(cache, "add", side_effect=RuntimeError("cache unavailable"))
    def test_code_request_fails_closed_when_cache_is_unavailable(self, cache_add):
        response = self.post_json(
            reverse("api_kiosk_request_code", args=[self.alpha_kiosk.pk]),
            {"email": self.mentor_user.email},
        )
        self.assertEqual(response.status_code, 503)
        cache_add.assert_called_once()

    @patch("allauth.account.adapter.get_adapter")
    def test_failed_email_and_cache_cleanup_leave_code_unusable(self, get_adapter):
        adapter = get_adapter.return_value
        adapter.generate_login_code.return_value = "654321"
        adapter.send_mail.side_effect = RuntimeError("delivery unavailable")
        with patch.object(
            cache, "delete", side_effect=RuntimeError("cache unavailable")
        ) as delete:
            response = self.post_json(
                reverse("api_kiosk_request_code", args=[self.alpha_kiosk.pk]),
                {"email": self.mentor_user.email},
            )
        self.assertEqual(response.status_code, 503)
        delete.assert_not_called()
        response = self.post_json(
            reverse("api_kiosk_unlock", args=[self.alpha_kiosk.pk]),
            {"email": self.mentor_user.email, "code": "654321"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertNotIn(f"kiosk_unlocked_{self.alpha_kiosk.pk}", response.cookies)

    @patch("allauth.account.adapter.get_adapter")
    def test_failed_older_delivery_does_not_remove_newer_code(self, get_adapter):
        adapter = get_adapter.return_value
        adapter.generate_login_code.side_effect = ["111111", "222222"]
        request_url = reverse("api_kiosk_request_code", args=[self.alpha_kiosk.pk])
        email = self.mentor_user.email

        def send_mail(template, recipient, context):
            if context["code"] == "111111":
                newer = self.post_json(request_url, {"email": email})
                self.assertEqual(newer.status_code, 200)
                raise RuntimeError("older delivery failed")

        adapter.send_mail.side_effect = send_mail
        response = self.post_json(request_url, {"email": email})
        self.assertEqual(response.status_code, 503)
        response = self.post_json(
            reverse("api_kiosk_unlock", args=[self.alpha_kiosk.pk]),
            {"email": email, "code": "222222"},
        )
        self.assertEqual(response.status_code, 200)

    @patch("allauth.account.adapter.get_adapter")
    def test_successful_older_delivery_cannot_replace_newer_code(self, get_adapter):
        adapter = get_adapter.return_value
        adapter.generate_login_code.side_effect = ["111111", "222222"]
        request_url = reverse("api_kiosk_request_code", args=[self.alpha_kiosk.pk])
        email = self.mentor_user.email

        def send_mail(template, recipient, context):
            if context["code"] == "111111":
                newer = self.post_json(request_url, {"email": email})
                self.assertEqual(newer.status_code, 200)

        adapter.send_mail.side_effect = send_mail
        response = self.post_json(request_url, {"email": email})
        self.assertEqual(response.status_code, 200)

        unlock_url = reverse("api_kiosk_unlock", args=[self.alpha_kiosk.pk])
        older_code = self.post_json(unlock_url, {"email": email, "code": "111111"})
        self.assertEqual(older_code.status_code, 403)
        self.assertNotIn(f"kiosk_unlocked_{self.alpha_kiosk.pk}", older_code.cookies)

        newer_code = self.post_json(unlock_url, {"email": email, "code": "222222"})
        self.assertEqual(newer_code.status_code, 200)

    @patch("allauth.account.adapter.get_adapter")
    def test_code_request_fails_closed_when_readiness_cache_write_fails(
        self, get_adapter
    ):
        email = self.mentor_user.email
        get_adapter.return_value.generate_login_code.return_value = "654321"
        original_add = cache.add
        cache_key = _otp_cache_key(self.alpha_kiosk, email)

        def fail_readiness_marker(key, value, timeout=None, **kwargs):
            if key.startswith(f"{cache_key}_ready_"):
                raise RuntimeError("cache unavailable")
            return original_add(key, value, timeout, **kwargs)

        with patch.object(cache, "add", side_effect=fail_readiness_marker):
            response = self.post_json(
                reverse("api_kiosk_request_code", args=[self.alpha_kiosk.pk]),
                {"email": email},
            )

        self.assertEqual(response.status_code, 503)
        get_adapter.return_value.send_mail.assert_called_once()
        unlock = self.post_json(
            reverse("api_kiosk_unlock", args=[self.alpha_kiosk.pk]),
            {"email": email, "code": "654321"},
        )
        self.assertEqual(unlock.status_code, 403)
        self.assertNotIn(f"kiosk_unlocked_{self.alpha_kiosk.pk}", unlock.cookies)

    def test_expired_code_cannot_unlock_even_with_readiness_marker(self):
        email = self.mentor_user.email
        cache_key = _otp_cache_key(self.alpha_kiosk, email)
        nonce = secrets.token_urlsafe(16)
        cache.set(
            cache_key,
            {"code": "654321", "nonce": nonce, "user_id": self.mentor_user.pk},
            timeout=0,
        )
        cache.set(
            _otp_ready_cache_key(self.alpha_kiosk, email, nonce), True, timeout=600
        )

        response = self.post_json(
            reverse("api_kiosk_unlock", args=[self.alpha_kiosk.pk]),
            {"email": email, "code": "654321"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertNotIn(f"kiosk_unlocked_{self.alpha_kiosk.pk}", response.cookies)

    @patch("allauth.account.adapter.get_adapter")
    def test_code_redemption_rechecks_membership_and_does_not_unlock(self, get_adapter):
        cache_key = _otp_cache_key(self.alpha_kiosk, self.mentor_user.email)
        nonce = secrets.token_urlsafe(16)
        cache.set(
            cache_key,
            {
                "code": "654321",
                "nonce": nonce,
                "user_id": self.mentor_user.pk,
            },
            600,
        )
        cache.set(
            _otp_ready_cache_key(self.alpha_kiosk, self.mentor_user.email, nonce),
            True,
            600,
        )
        self.membership.is_active = False
        self.membership.save(update_fields=["is_active"])
        response = self.post_json(
            reverse("api_kiosk_unlock", args=[self.alpha_kiosk.pk]),
            {"email": self.mentor_user.email, "code": "654321"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertNotIn(f"kiosk_unlocked_{self.alpha_kiosk.pk}", response.cookies)
        self.assertIsNotNone(cache.get(f"{cache_key}_redeeming_{nonce}"))
        self.membership.is_active = True
        self.membership.save(update_fields=["is_active"])
        response = self.post_json(
            reverse("api_kiosk_unlock", args=[self.alpha_kiosk.pk]),
            {"email": self.mentor_user.email, "code": "654321"},
        )
        self.assertEqual(response.status_code, 403)
        get_adapter.return_value.send_mail.assert_not_called()

    @override_settings(SESSION_COOKIE_SECURE=True)
    @patch("allauth.account.adapter.get_adapter")
    def test_unlock_code_is_one_use_and_cookie_is_host_only(self, get_adapter):
        get_adapter.return_value.generate_login_code.return_value = "654321"
        self.assertEqual(
            self.post_json(
                reverse("api_kiosk_request_code", args=[self.alpha_kiosk.pk]),
                {"email": self.mentor_user.email},
            ).status_code,
            200,
        )
        unlock_url = reverse("api_kiosk_unlock", args=[self.alpha_kiosk.pk])
        payload = {"email": self.mentor_user.email, "code": "654321"}
        response = self.post_json(unlock_url, payload)
        self.assertEqual(response.status_code, 200)
        cookie = response.cookies[f"kiosk_unlocked_{self.alpha_kiosk.pk}"]
        self.assertTrue(cookie["httponly"])
        self.assertTrue(cookie["secure"])
        self.assertEqual(cookie["samesite"], "Lax")
        self.assertEqual(cookie["domain"], "")
        self.assertNotEqual(cookie.value, "1")
        cache_key = _otp_cache_key(self.alpha_kiosk, self.mentor_user.email)
        nonce = cache.get(cache_key)["nonce"]
        self.assertIsNotNone(cache.get(f"{cache_key}_redeeming_{nonce}"))
        response = self.post_json(unlock_url, payload)
        self.assertEqual(response.status_code, 403)

    @patch("allauth.account.adapter.get_adapter")
    def test_overlapping_code_redemption_only_unlocks_once(self, get_adapter):
        get_adapter.return_value.generate_login_code.return_value = "654321"
        request_url = reverse("api_kiosk_request_code", args=[self.alpha_kiosk.pk])
        self.assertEqual(
            self.post_json(request_url, {"email": self.mentor_user.email}).status_code,
            200,
        )
        unlock_url = reverse("api_kiosk_unlock", args=[self.alpha_kiosk.pk])
        payload = {"email": self.mentor_user.email, "code": "654321"}
        overlapping_statuses = []

        def recheck_with_overlap(email, organization):
            overlapping_statuses.append(self.post_json(unlock_url, payload).status_code)
            return self.mentor_user

        with patch(
            "api.kiosk_views._get_eligible_unlock_user",
            side_effect=recheck_with_overlap,
        ):
            response = self.post_json(unlock_url, payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(overlapping_statuses, [403])

    @patch("allauth.account.adapter.get_adapter")
    def test_code_cannot_be_redeemed_by_another_account_with_same_email(
        self, get_adapter
    ):
        get_adapter.return_value.generate_login_code.return_value = "654321"
        email = self.mentor_user.email
        self.assertEqual(
            self.post_json(
                reverse("api_kiosk_request_code", args=[self.alpha_kiosk.pk]),
                {"email": email},
            ).status_code,
            200,
        )
        self.mentor_user.email = "changed@alpha.example"
        self.mentor_user.save(update_fields=["email"])
        User.objects.create_superuser(username="new-email-owner", email=email)
        response = self.post_json(
            reverse("api_kiosk_unlock", args=[self.alpha_kiosk.pk]),
            {"email": email, "code": "654321"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertNotIn(f"kiosk_unlocked_{self.alpha_kiosk.pk}", response.cookies)

    @patch("api.kiosk_views._acquire_redemption_lock", return_value=None)
    def test_redemption_fails_closed_when_one_use_marker_is_unavailable(self, acquire):
        cache_key = _otp_cache_key(self.alpha_kiosk, self.mentor_user.email)
        cache.set(
            cache_key,
            {
                "code": "654321",
                "nonce": secrets.token_urlsafe(16),
                "user_id": self.mentor_user.pk,
            },
            600,
        )
        record = cache.get(cache_key)
        cache.set(
            _otp_ready_cache_key(
                self.alpha_kiosk, self.mentor_user.email, record["nonce"]
            ),
            True,
            600,
        )
        response = self.post_json(
            reverse("api_kiosk_unlock", args=[self.alpha_kiosk.pk]),
            {"email": self.mentor_user.email, "code": "654321"},
        )
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(f"kiosk_unlocked_{self.alpha_kiosk.pk}", response.cookies)
        acquire.assert_called_once()

    @patch.object(cache, "add", side_effect=RuntimeError("cache unavailable"))
    def test_code_redemption_fails_closed_when_cache_is_unavailable(self, cache_add):
        response = self.post_json(
            reverse("api_kiosk_unlock", args=[self.alpha_kiosk.pk]),
            {"email": self.mentor_user.email, "code": "654321"},
        )
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(f"kiosk_unlocked_{self.alpha_kiosk.pk}", response.cookies)
        self.assertGreaterEqual(cache_add.call_count, 1)

    def test_tap_rejects_person_id_from_another_organization(self):
        foreign_student = Student.objects.create(
            organization=self.beta,
            legal_first_name="Foreign",
            last_name="Student",
        )
        self.set_unlocked(self.alpha_kiosk)
        response = self.post_json(
            reverse("api_kiosk_tap", args=[self.alpha_kiosk.pk]),
            {"student_id": foreign_student.pk},
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(AttendanceEvent.objects.exists())

    def test_foreign_owned_enrollment_cannot_authorize_lookup_or_tap(self):
        student = Student.objects.create(
            organization=self.alpha, legal_first_name="Disallowed", last_name="Student"
        )
        Enrollment.objects.create(
            organization=self.beta,
            student=student,
            program=self.alpha_program,
            active=True,
        )
        self.set_unlocked(self.alpha_kiosk)
        response = self.client.get(
            reverse("api_kiosk_lookup", args=[self.alpha_kiosk.pk]),
            {"name": "Disallowed Student"},
            HTTP_HOST=self.alpha_host,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["students"], [])
        response = self.post_json(
            reverse("api_kiosk_tap", args=[self.alpha_kiosk.pk]),
            {"student_id": student.pk},
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(AttendanceEvent.objects.exists())

    def test_service_rejects_person_from_another_organization(self):
        foreign_student = Student.objects.create(
            organization=self.beta,
            legal_first_name="Foreign",
            last_name="Student",
        )
        with self.assertRaises(PermissionDenied):
            record_tap(program=self.alpha_program, student=foreign_student)
        self.assertFalse(AttendanceEvent.objects.exists())

    def test_foreign_rfid_lookup_does_not_normalize_or_reveal_card(self):
        foreign_student = Student.objects.create(
            organization=self.beta,
            legal_first_name="Foreign",
            last_name="Student",
        )
        card = RFIDCard.objects.create(
            organization=self.beta,
            uid="12345",
            student=foreign_student,
        )
        self.set_unlocked(self.alpha_kiosk)
        response = self.client.get(
            reverse("api_kiosk_lookup", args=[self.alpha_kiosk.pk]),
            {"rfid": "00012345"},
            HTTP_HOST=self.alpha_host,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["students"], [])
        card.refresh_from_db()
        self.assertEqual(card.uid, "12345")

    def test_inconsistent_rfid_owner_is_not_normalized_or_revealed(self):
        foreign_student = Student.objects.create(
            organization=self.beta,
            legal_first_name="Foreign",
            last_name="Student",
        )
        card = RFIDCard.objects.create(
            organization=self.alpha,
            uid="54321",
            student=foreign_student,
        )
        self.set_unlocked(self.alpha_kiosk)
        response = self.client.get(
            reverse("api_kiosk_lookup", args=[self.alpha_kiosk.pk]),
            {"rfid": "00054321"},
            HTTP_HOST=self.alpha_host,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["students"], [])
        card.refresh_from_db()
        self.assertEqual(card.uid, "54321")

    def test_tap_records_event_and_session_for_program_organization(self):
        self.set_unlocked(self.alpha_kiosk)
        response = self.post_json(
            reverse("api_kiosk_tap", args=[self.alpha_kiosk.pk]),
            {"visitor_name": "Alpha Visitor"},
        )
        self.assertEqual(response.status_code, 200)
        event = AttendanceEvent.objects.get()
        session = AttendanceSession.objects.get()
        self.assertEqual(event.organization, self.alpha)
        self.assertEqual(session.organization, self.alpha)

    def test_student_checkout_returns_attendance_hours(self):
        self.set_unlocked(self.alpha_kiosk)
        url = reverse("api_kiosk_tap", args=[self.alpha_kiosk.pk])
        payload = {"student_id": self.student.pk}
        self.assertEqual(self.post_json(url, payload).status_code, 200)
        response = self.post_json(url, payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["event_type"], "OUT")
        self.assertIn("session_hours", response.json())
        self.assertIsNotNone(
            AttendanceSession.objects.get(student=self.student).check_out
        )

    def test_who_is_here_omits_inconsistent_foreign_organization_session(self):
        AttendanceSession.objects.create(
            organization=self.beta,
            program=self.alpha_program,
            visitor_name="Foreign organization session",
            check_in="2026-01-01T10:00:00Z",
        )
        self.set_unlocked(self.alpha_kiosk)
        response = self.client.get(
            reverse("api_kiosk_who_is_here", args=[self.alpha_kiosk.pk]),
            HTTP_HOST=self.alpha_host,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["people"], [])

    def test_who_is_here_omits_session_linked_to_foreign_person(self):
        foreign_student = Student.objects.create(
            organization=self.beta, legal_first_name="Foreign", last_name="Student"
        )
        AttendanceSession.objects.create(
            organization=self.alpha,
            program=self.alpha_program,
            student=foreign_student,
            check_in=timezone.now(),
        )
        self.set_unlocked(self.alpha_kiosk)
        response = self.client.get(
            reverse("api_kiosk_who_is_here", args=[self.alpha_kiosk.pk]),
            HTTP_HOST=self.alpha_host,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["people"], [])

    def test_tap_does_not_close_stale_session_linked_to_foreign_person(self):
        foreign_student = Student.objects.create(
            organization=self.beta, legal_first_name="Foreign", last_name="Student"
        )
        session = AttendanceSession.objects.create(
            organization=self.alpha,
            program=self.alpha_program,
            student=foreign_student,
            visitor_name="Alpha Visitor",
            check_in=timezone.now() - timedelta(days=1),
        )
        self.set_unlocked(self.alpha_kiosk)
        response = self.post_json(
            reverse("api_kiosk_tap", args=[self.alpha_kiosk.pk]),
            {"visitor_name": "Alpha Visitor"},
        )
        self.assertEqual(response.status_code, 200)
        session.refresh_from_db()
        self.assertIsNone(session.check_out)
