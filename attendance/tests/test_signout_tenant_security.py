import base64
import json
from types import SimpleNamespace
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.contrib.auth.models import User
from django.core.cache import cache
from django.http import Http404, HttpResponse
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from attendance.models import (
    DigitalSignout,
    DigitalSignoutConfig,
    StudentPresence,
)
from attendance.signout_utils import (
    _CODE_ATTEMPT_LIMIT,
    _CODE_REQUEST_IP_LIMIT,
    _CODE_REQUEST_LIMIT,
    _is_unlocked,
    _otp_cache_key,
    _set_unlocked,
)
from attendance.signout_views import _get_config_or_404
from organizations.models import Organization, UserOrganizationMembership
from programs.models import (
    Adult,
    AdultStudentRelationship,
    Enrollment,
    Group,
    Program,
    Student,
    Team,
)

TEST_FILE_ENCRYPTION_KEY = Fernet.generate_key().decode()


@override_settings(
    ALLOWED_HOSTS=[".momentumteam.app", "testserver"],
    MOMENTUM_ROOT_DOMAIN="momentumteam.app",
    FILE_ENCRYPTION_KEY=TEST_FILE_ENCRYPTION_KEY,
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "signout-tenant-security-tests",
        }
    },
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    },
)
class SignoutTenantSecurityTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()
        self.alpha = Organization.objects.create(
            name="Alpha", slug="signout-alpha", subdomain="signout-alpha"
        )
        self.beta = Organization.objects.create(
            name="Beta", slug="signout-beta", subdomain="signout-beta"
        )
        self.alpha_host = "signout-alpha.momentumteam.app"
        self.beta_host = "signout-beta.momentumteam.app"
        self.alpha_program = Program.objects.create(
            organization=self.alpha, name="Alpha Sign-out Program"
        )
        self.beta_program = Program.objects.create(
            organization=self.beta, name="Beta Sign-out Program"
        )
        self.alpha_config = DigitalSignoutConfig.objects.create(
            organization=self.alpha,
            program=self.alpha_program,
            label="Alpha Station",
        )
        self.beta_config = DigitalSignoutConfig.objects.create(
            organization=self.beta,
            program=self.beta_program,
            label="Beta Station",
        )
        self.mentor, self.membership = self.make_mentor(
            "alpha-mentor", "mentor@alpha.example", self.alpha
        )
        self.student = Student.objects.create(
            organization=self.alpha,
            legal_first_name="Ada",
            last_name="Student",
        )
        Enrollment.objects.create(
            organization=self.alpha,
            student=self.student,
            program=self.alpha_program,
            active=True,
        )
        StudentPresence.objects.create(
            organization=self.alpha,
            program=self.alpha_program,
            student=self.student,
            status=StudentPresence.PRESENT,
        )

    @staticmethod
    def make_mentor(username, email, organization, role="Mentor", active=True):
        user = User.objects.create_user(username=username, email=email)
        Adult.objects.create(
            organization=organization,
            user=user,
            legal_first_name=username,
            last_name="Mentor",
            is_mentor=True,
            mentor_active=True,
            login_enabled=True,
        )
        membership, _ = UserOrganizationMembership.objects.get_or_create(
            user=user,
            organization=organization,
            role=role,
            defaults={"is_active": active},
        )
        if membership.is_active != active:
            membership.is_active = active
            membership.save(update_fields=["is_active"])
        return user, membership

    def post_json(self, url, payload, host=None):
        return self.client.post(
            url,
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_HOST=host or self.alpha_host,
        )

    @patch("api.signout_views.get_adapter")
    def test_station_cannot_be_requested_from_another_tenant(self, get_adapter):
        get_adapter.return_value.generate_login_code.return_value = "161803"
        get_adapter.return_value.send_mail.return_value = None
        response = self.post_json(
            reverse("api_signout_request_code", args=[self.alpha_config.pk]),
            {"email": self.mentor.email},
            host=self.beta_host,
        )
        self.assertIn(response.status_code, (302, 403, 404))
        get_adapter.assert_not_called()

    def test_missing_organization_context_fails_closed(self):
        with self.assertRaises(Http404):
            _get_config_or_404(self.alpha_config.pk, None)

    def test_config_with_foreign_program_is_not_returned(self):
        inconsistent_config = DigitalSignoutConfig.objects.create(
            organization=self.alpha,
            program=self.beta_program,
            label="Mismatched API Station",
        )
        with self.assertRaises(Http404):
            _get_config_or_404(inconsistent_config.pk, self.alpha)

    @patch("api.signout_views.get_adapter")
    def test_code_request_requires_active_tenant_membership(self, get_adapter):
        get_adapter.return_value.generate_login_code.return_value = "271828"
        unassigned_mentor, _ = self.make_mentor(
            "unassigned-mentor", "unassigned@example.test", self.alpha, active=False
        )
        response = self.post_json(
            reverse("api_signout_request_code", args=[self.alpha_config.pk]),
            {"email": unassigned_mentor.email},
        )
        self.assertEqual(response.status_code, 403)
        get_adapter.assert_not_called()

    def test_code_request_does_not_provision_an_account_for_profile_email(self):
        email = "profile-only@example.test"
        Adult.objects.create(
            organization=self.alpha,
            legal_first_name="Profile",
            last_name="Only",
            andrew_email=email,
            is_mentor=True,
            mentor_active=True,
            login_enabled=True,
        )
        response = self.post_json(
            reverse("api_signout_request_code", args=[self.alpha_config.pk]),
            {"email": email},
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(User.objects.filter(email__iexact=email).exists())

    def test_code_request_denies_nonmentor_membership(self):
        parent = User.objects.create_user(
            username="alpha-parent", email="parent@alpha.example"
        )
        Adult.objects.create(
            organization=self.alpha,
            user=parent,
            legal_first_name="Alpha",
            last_name="Parent",
            is_parent=True,
        )
        UserOrganizationMembership.objects.get_or_create(
            user=parent,
            organization=self.alpha,
            role="Parent",
            defaults={"is_active": True},
        )
        response = self.post_json(
            reverse("api_signout_request_code", args=[self.alpha_config.pk]),
            {"email": parent.email},
        )
        self.assertEqual(response.status_code, 403)

    @patch("api.signout_views.get_adapter")
    def test_active_platform_superuser_can_request_and_redeem(self, get_adapter):
        adapter = get_adapter.return_value
        adapter.generate_login_code.return_value = "424242"
        adapter.send_mail.return_value = None
        user = User.objects.create_superuser(
            username="platform-support", email="support@example.test", password=None
        )
        request_url = reverse("api_signout_request_code", args=[self.alpha_config.pk])
        unlock_url = reverse("api_signout_unlock", args=[self.alpha_config.pk])
        requested = self.post_json(request_url, {"email": user.email})
        self.assertEqual(requested.status_code, 200)
        unlocked = self.post_json(unlock_url, {"email": user.email, "code": "424242"})
        self.assertEqual(unlocked.status_code, 200)

    @patch("api.signout_views.get_adapter")
    def test_overlapping_code_requests_keep_only_the_latest_code_usable(
        self, get_adapter
    ):
        adapter = get_adapter.return_value
        adapter.generate_login_code.side_effect = ["111111", "222222"]
        adapter.send_mail.side_effect = lambda template, recipient, context: (
            self.assertEqual(
                self.post_json(
                    reverse("api_signout_request_code", args=[self.alpha_config.pk]),
                    {"email": self.mentor.email},
                ).status_code,
                200,
            )
            if context["code"] == "111111"
            else None
        )
        request_url = reverse("api_signout_request_code", args=[self.alpha_config.pk])
        requested = self.post_json(request_url, {"email": self.mentor.email})
        self.assertEqual(requested.status_code, 200)
        unlock_url = reverse("api_signout_unlock", args=[self.alpha_config.pk])
        old_code = self.post_json(
            unlock_url, {"email": self.mentor.email, "code": "111111"}
        )
        current_code = self.post_json(
            unlock_url, {"email": self.mentor.email, "code": "222222"}
        )
        self.assertEqual(old_code.status_code, 403)
        self.assertEqual(current_code.status_code, 200)

    @patch("api.signout_views.get_adapter")
    def test_email_failure_leaves_code_unredeemable(self, get_adapter):
        adapter = get_adapter.return_value
        adapter.generate_login_code.return_value = "535353"
        adapter.send_mail.side_effect = RuntimeError("delivery failed")
        request_url = reverse("api_signout_request_code", args=[self.alpha_config.pk])
        response = self.post_json(request_url, {"email": self.mentor.email})
        self.assertEqual(response.status_code, 503)
        unlock_url = reverse("api_signout_unlock", args=[self.alpha_config.pk])
        rejected = self.post_json(
            unlock_url, {"email": self.mentor.email, "code": "535353"}
        )
        self.assertEqual(rejected.status_code, 403)

    @patch("api.signout_views.get_adapter")
    @patch("api.signout_views._increment_rate_limit", return_value=None)
    def test_cache_error_fails_closed_during_code_request(
        self, increment_rate_limit, get_adapter
    ):
        response = self.post_json(
            reverse("api_signout_request_code", args=[self.alpha_config.pk]),
            {"email": self.mentor.email},
        )
        self.assertEqual(response.status_code, 503)
        get_adapter.assert_not_called()
        increment_rate_limit.assert_called_once()

    @patch("api.signout_views.get_adapter")
    def test_request_rate_limit_is_enforced(self, get_adapter):
        adapter = get_adapter.return_value
        adapter.generate_login_code.return_value = "123456"
        adapter.send_mail.return_value = None
        request_url = reverse("api_signout_request_code", args=[self.alpha_config.pk])
        responses = [
            self.post_json(request_url, {"email": self.mentor.email})
            for _ in range(_CODE_REQUEST_LIMIT + 1)
        ]
        self.assertEqual(
            [response.status_code for response in responses[:-1]],
            [200] * _CODE_REQUEST_LIMIT,
        )
        self.assertEqual(responses[-1].status_code, 429)
        self.assertEqual(adapter.send_mail.call_count, _CODE_REQUEST_LIMIT)

    def test_unknown_emails_are_limited_by_request_ip(self):
        url = reverse("api_signout_request_code", args=[self.alpha_config.pk])
        for index in range(_CODE_REQUEST_IP_LIMIT):
            response = self.post_json(url, {"email": f"unknown{index}@example.test"})
            self.assertEqual(response.status_code, 403)
        response = self.post_json(url, {"email": "another@example.test"})
        self.assertEqual(response.status_code, 429)

    def test_unknown_emails_are_limited_by_attempt_ip(self):
        url = reverse("api_signout_unlock", args=[self.alpha_config.pk])
        for index in range(_CODE_ATTEMPT_LIMIT * 5):
            response = self.post_json(
                url, {"email": f"unknown{index}@example.test", "code": "000000"}
            )
            self.assertEqual(response.status_code, 403)
        response = self.post_json(
            url, {"email": "another@example.test", "code": "000000"}
        )
        self.assertEqual(response.status_code, 429)

    @patch("api.signout_views.get_adapter")
    def test_code_attempt_rate_limit_is_enforced(self, get_adapter):
        adapter = get_adapter.return_value
        adapter.generate_login_code.return_value = "123456"
        adapter.send_mail.return_value = None
        self.post_json(
            reverse("api_signout_request_code", args=[self.alpha_config.pk]),
            {"email": self.mentor.email},
        )
        unlock_url = reverse("api_signout_unlock", args=[self.alpha_config.pk])
        responses = [
            self.post_json(unlock_url, {"email": self.mentor.email, "code": "000000"})
            for _ in range(_CODE_ATTEMPT_LIMIT + 1)
        ]
        self.assertEqual(
            [response.status_code for response in responses[:-1]],
            [403] * _CODE_ATTEMPT_LIMIT,
        )
        self.assertEqual(responses[-1].status_code, 429)

    @patch("api.signout_views.get_adapter")
    def test_expired_code_is_rejected(self, get_adapter):
        adapter = get_adapter.return_value
        adapter.generate_login_code.return_value = "123456"
        adapter.send_mail.return_value = None
        self.post_json(
            reverse("api_signout_request_code", args=[self.alpha_config.pk]),
            {"email": self.mentor.email},
        )
        cache_key = _otp_cache_key(self.alpha_config, self.mentor.email)
        cache.set(cache_key, cache.get(cache_key), timeout=0)
        response = self.post_json(
            reverse("api_signout_unlock", args=[self.alpha_config.pk]),
            {"email": self.mentor.email, "code": "123456"},
        )
        self.assertEqual(response.status_code, 403)

    @patch("api.signout_views.get_adapter")
    def test_cache_error_fails_closed_during_redemption(self, get_adapter):
        adapter = get_adapter.return_value
        adapter.generate_login_code.return_value = "123456"
        adapter.send_mail.return_value = None
        self.post_json(
            reverse("api_signout_request_code", args=[self.alpha_config.pk]),
            {"email": self.mentor.email},
        )
        with patch.object(cache, "get", side_effect=RuntimeError("cache down")):
            response = self.post_json(
                reverse("api_signout_unlock", args=[self.alpha_config.pk]),
                {"email": self.mentor.email, "code": "123456"},
            )
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(f"signout_unlocked_{self.alpha_config.pk}", response.cookies)

    @patch("api.signout_views.get_adapter")
    def test_membership_is_rechecked_when_code_is_redeemed(self, get_adapter):
        adapter = get_adapter.return_value
        adapter.generate_login_code.return_value = "314159"
        adapter.send_mail.return_value = None
        request_url = reverse("api_signout_request_code", args=[self.alpha_config.pk])
        unlock_url = reverse("api_signout_unlock", args=[self.alpha_config.pk])
        requested = self.post_json(request_url, {"email": self.mentor.email})
        self.assertEqual(requested.status_code, 200)

        self.membership.is_active = False
        self.membership.save(update_fields=["is_active"])
        response = self.post_json(
            unlock_url, {"email": self.mentor.email, "code": "314159"}
        )
        self.assertEqual(response.status_code, 403)
        self.assertNotIn(f"signout_unlocked_{self.alpha_config.pk}", response.cookies)

    def test_legacy_plain_cookie_does_not_unlock_station(self):
        cookie_name = f"signout_unlocked_{self.alpha_config.pk}"
        self.client.cookies[cookie_name] = "1"
        response = self.client.get(
            reverse("digital_signout", args=[self.alpha_config.pk]),
            HTTP_HOST=self.alpha_host,
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["is_unlocked"])

    def test_cookie_is_signed_and_tenant_bound(self):
        response = HttpResponse()
        _set_unlocked(response, self.alpha_config)
        cookie_name = f"signout_unlocked_{self.alpha_config.pk}"
        token = response.cookies[cookie_name].value
        self.assertTrue(
            _is_unlocked(
                SimpleNamespace(COOKIES={cookie_name: token}), self.alpha_config
            )
        )
        mismatched_tenant = SimpleNamespace(
            pk=self.alpha_config.pk, organization_id=self.beta.pk
        )
        self.assertFalse(
            _is_unlocked(
                SimpleNamespace(COOKIES={cookie_name: token}), mismatched_tenant
            )
        )
        self.assertFalse(
            _is_unlocked(
                SimpleNamespace(COOKIES={cookie_name: f"{token}tampered"}),
                self.alpha_config,
            )
        )
        self.assertEqual(response.cookies[cookie_name]["domain"], "")
        self.assertTrue(response.cookies[cookie_name]["httponly"])

    @patch("attendance.signout_utils._COOKIE_MAX_AGE", -1)
    def test_expired_signed_cookie_is_rejected(self):
        response = HttpResponse()
        _set_unlocked(response, self.alpha_config)
        cookie_name = f"signout_unlocked_{self.alpha_config.pk}"
        self.assertFalse(
            _is_unlocked(
                SimpleNamespace(
                    COOKIES={cookie_name: response.cookies[cookie_name].value}
                ),
                self.alpha_config,
            )
        )

    def test_config_with_foreign_program_is_not_served(self):
        inconsistent_config = DigitalSignoutConfig.objects.create(
            organization=self.alpha,
            program=self.beta_program,
            label="Mismatched Station",
        )
        self.client.force_login(self.mentor)
        response = self.client.get(
            reverse("digital_signout", args=[inconsistent_config.pk]),
            HTTP_HOST=self.alpha_host,
        )
        self.assertNotEqual(response.status_code, 200)

    def test_public_station_does_not_show_foreign_organization_student(self):
        foreign_student = Student.objects.create(
            organization=self.beta,
            legal_first_name="Foreign",
            last_name="Student",
        )
        Enrollment.objects.create(
            organization=self.alpha,
            student=foreign_student,
            program=self.alpha_program,
            active=True,
        )
        StudentPresence.objects.create(
            organization=self.beta,
            program=self.alpha_program,
            student=foreign_student,
            status=StudentPresence.PRESENT,
        )
        self.unlock_station(self.alpha_config)
        response = self.client.get(
            reverse("digital_signout", args=[self.alpha_config.pk]),
            HTTP_HOST=self.alpha_host,
        )
        self.assertNotContains(response, "Foreign Student")

    def test_parent_picker_scopes_adults_and_relationships(self):
        allowed = Adult.objects.create(
            organization=self.alpha,
            legal_first_name="Allowed",
            last_name="Guardian",
            is_parent=True,
        )
        foreign_link = Adult.objects.create(
            organization=self.alpha,
            legal_first_name="Foreignlink",
            last_name="Guardian",
            is_parent=True,
        )
        foreign_adult = Adult.objects.create(
            organization=self.beta,
            legal_first_name="Foreignadult",
            last_name="Guardian",
            is_parent=True,
        )
        AdultStudentRelationship.objects.create(
            organization=self.alpha, adult=allowed, student=self.student
        )
        AdultStudentRelationship.objects.create(
            organization=self.beta, adult=foreign_link, student=self.student
        )
        AdultStudentRelationship.objects.create(
            organization=self.alpha, adult=foreign_adult, student=self.student
        )
        self.unlock_station(self.alpha_config)
        response = self.client.get(
            reverse("digital_signout", args=[self.alpha_config.pk]),
            HTTP_HOST=self.alpha_host,
        )
        self.assertContains(response, "Allowed Guardian")
        self.assertNotContains(response, "Foreignlink Guardian")
        self.assertNotContains(response, "Foreignadult Guardian")

    def unlock_station(self, config):
        response = HttpResponse()
        _set_unlocked(response, config)
        cookie_name = f"signout_unlocked_{config.pk}"
        self.client.cookies[cookie_name] = response.cookies[cookie_name].value

    def test_foreign_tenant_cannot_mark_another_program_present(self):
        beta_mentor, _ = self.make_mentor(
            "beta-mentor", "mentor@beta.example", self.beta
        )
        self.client.force_login(beta_mentor)
        response = self.client.post(
            reverse("program_digital_signout", args=[self.alpha_program.pk]),
            {"action": "mark_absent", "student_id": self.student.pk},
            HTTP_HOST=self.beta_host,
        )
        self.assertNotEqual(response.status_code, 200)
        self.assertEqual(
            StudentPresence.objects.get(
                program=self.alpha_program, student=self.student
            ).status,
            StudentPresence.PRESENT,
        )

    def test_management_excludes_signout_linked_to_foreign_station(self):
        inconsistent = DigitalSignout.objects.create(
            organization=self.alpha,
            config=self.beta_config,
            program=self.alpha_program,
            student=self.student,
            signed_by_name="Foreign Station Record",
        )
        lead, _ = self.make_mentor(
            "alpha-lead", "lead@alpha.example", self.alpha, role="LeadMentor"
        )
        self.client.force_login(lead)
        response = self.client.get(
            reverse("program_digital_signout", args=[self.alpha_program.pk]),
            HTTP_HOST=self.alpha_host,
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(inconsistent, list(response.context["recent_signouts"]))

    def test_management_hides_foreign_enrollment_groups(self):
        local_team = Team.objects.create(
            organization=self.alpha, team_type="FRC", number=5678, name="Local Team"
        )
        foreign_team = Team.objects.create(
            organization=self.beta, team_type="FRC", number=1234, name="Foreign Team"
        )
        local_groups = [
            Group.objects.create(
                organization=self.alpha,
                program=self.alpha_program,
                name="Local Crew",
            ),
            Group.objects.create(
                organization=self.alpha,
                program=self.alpha_program,
                name="Local Subteam",
            ),
        ]
        foreign_group = Group.objects.create(
            organization=self.beta,
            program=self.beta_program,
            name="Foreign Group",
        )
        lead, _ = self.make_mentor(
            "alpha-lead", "lead@alpha.example", self.alpha, role="LeadMentor"
        )
        self.client.force_login(lead)
        enrollment = Enrollment.objects.get(
            program=self.alpha_program, student=self.student
        )
        url = reverse("program_digital_signout", args=[self.alpha_program.pk])

        enrollment.team = local_team
        enrollment.save(update_fields=["team"])
        enrollment.groups.set(local_groups)
        response = self.client.get(url, HTTP_HOST=self.alpha_host)
        self.assertContains(response, "Ada Student")
        self.assertContains(response, "Local Team")
        for group in local_groups:
            self.assertContains(response, group.name)

        enrollment.team = foreign_team
        enrollment.save(update_fields=["team"])
        enrollment.groups.add(foreign_group)
        response = self.client.get(url, HTTP_HOST=self.alpha_host)
        self.assertContains(response, "Ada Student")
        self.assertNotContains(response, "Foreign Team")
        self.assertNotContains(response, foreign_group.name)
        for group in local_groups:
            self.assertContains(response, group.name)

    def test_presence_write_does_not_modify_foreign_owned_record(self):
        presence = StudentPresence.objects.get(
            program=self.alpha_program, student=self.student
        )
        presence.organization = self.beta
        presence.save(update_fields=["organization"])
        lead, _ = self.make_mentor(
            "alpha-lead", "lead@alpha.example", self.alpha, role="LeadMentor"
        )
        self.client.force_login(lead)
        url = reverse("program_digital_signout", args=[self.alpha_program.pk])
        response = self.client.post(
            url,
            {"action": "mark_absent", "student_id": self.student.pk},
            HTTP_HOST=self.alpha_host,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, url)
        presence.refresh_from_db()
        self.assertEqual(presence.organization, self.beta)
        self.assertEqual(presence.status, StudentPresence.PRESENT)

    def test_signout_record_is_owned_by_program_organization(self):
        self.unlock_station(self.alpha_config)
        signature = (
            "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAAB"
            "CAQAAAC1HAwCAAAAC0lEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
        )
        response = self.client.post(
            reverse("digital_signout", args=[self.alpha_config.pk]),
            {
                "student_id": self.student.pk,
                "signed_by_name": "Parent",
                "signature": signature,
            },
            HTTP_HOST=self.alpha_host,
        )
        self.assertEqual(response.status_code, 302)
        signout = DigitalSignout.objects.get()
        self.assertEqual(signout.organization, self.alpha)
        self.assertEqual(signout.config, self.alpha_config)
        self.assertEqual(signout.program, self.alpha_program)
        encrypted_signature = signout.signature.storage.open(
            signout.signature.name
        ).read()
        self.assertEqual(
            Fernet(TEST_FILE_ENCRYPTION_KEY.encode()).decrypt(encrypted_signature),
            base64.b64decode(signature.split(",", 1)[1]),
        )
