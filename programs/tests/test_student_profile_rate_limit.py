import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from audit.events import AuditEvent
from audit.models import AuditLog
from organizations.models import Organization, UserOrganizationMembership
from programs.models import Student
from programs.utils.profile_rate_limit import check_profile_rate_limit

RATE_LIMIT_SETTINGS = {
    "TESTING": True,
    "PROFILE_RATE_LIMIT_ENABLED": True,
    "PROFILE_RATE_LIMIT_COUNT": 2,
    "PROFILE_RATE_LIMIT_WINDOW_SECONDS": 60,
}


@override_settings(**RATE_LIMIT_SETTINGS)
class StudentProfileRateLimitViewTests(TestCase):
    def setUp(self):
        cache.clear()
        self.organization = Organization.objects.get(slug="gos")
        self.mentor = self._create_user("mentor", "Mentor")
        self.students = [
            Student.objects.create(
                organization=self.organization,
                legal_first_name=f"Student{index}",
                last_name="Tester",
            )
            for index in range(3)
        ]
        self.client.force_login(self.mentor)

    def _create_user(self, username, role, *, is_superuser=False):
        user = User.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            password="password123",
            is_staff=True,
            is_superuser=is_superuser,
        )  # nosec B106
        if not is_superuser:
            group, _ = Group.objects.get_or_create(name=role)
            user.groups.add(group)
            UserOrganizationMembership.objects.create(
                user=user,
                organization=self.organization,
                role=role,
                is_active=True,
            )
        return user

    def _get_student(self, student):
        return self.client.get(reverse("student_detail", kwargs={"pk": student.pk}))

    def test_requests_within_limit_succeed_and_are_audited(self):
        responses = [self._get_student(student) for student in self.students[:2]]

        self.assertEqual([response.status_code for response in responses], [200, 200])
        self.assertEqual(
            AuditLog.objects.filter(
                event=AuditEvent.SENSITIVE_DATA_VIEW,
                actor=self.mentor,
                resource_type="Student",
            ).count(),
            2,
        )

    def test_exceeded_limit_returns_429_retry_after_and_security_warning(self):
        for student in self.students[:2]:
            self.assertEqual(self._get_student(student).status_code, 200)

        with self.assertLogs(
            "programs.utils.profile_rate_limit", level=logging.WARNING
        ) as captured:
            response = self._get_student(self.students[2])

        self.assertEqual(response.status_code, 429)
        self.assertGreaterEqual(int(response["Retry-After"]), 1)
        self.assertLessEqual(int(response["Retry-After"]), 60)
        self.assertContains(response, "student directory", status_code=429)
        self.assertTrue(
            any("rate limit" in message.lower() for message in captured.output)
        )
        self.assertEqual(
            AuditLog.objects.filter(
                event=AuditEvent.SENSITIVE_DATA_VIEW,
                actor=self.mentor,
                resource_type="Student",
            ).count(),
            2,
        )

    @override_settings(PROFILE_RATE_LIMIT_COUNT=10)
    def test_more_than_fifteen_distinct_profiles_triggers_default_limit(self):
        students = list(self.students)
        students.extend(
            Student.objects.create(
                organization=self.organization,
                legal_first_name=f"Rapid{index}",
                last_name="Tester",
            )
            for index in range(13)
        )

        responses = [self._get_student(student) for student in students]

        self.assertEqual(sum(response.status_code == 200 for response in responses), 10)
        self.assertEqual(sum(response.status_code == 429 for response in responses), 6)

    def test_lead_mentors_are_exempt(self):
        lead = self._create_user("lead", "LeadMentor")
        self.client.force_login(lead)

        responses = [self._get_student(student) for student in self.students]

        self.assertEqual(
            [response.status_code for response in responses], [200, 200, 200]
        )

    def test_superusers_are_exempt(self):
        superuser = self._create_user("platform-admin", "LeadMentor", is_superuser=True)
        self.client.force_login(superuser)

        responses = [self._get_student(student) for student in self.students]

        self.assertEqual(
            [response.status_code for response in responses], [200, 200, 200]
        )

    @override_settings(TESTING=True, PROFILE_RATE_LIMIT_ENABLED=False)
    def test_rate_limit_is_disabled_during_tests_by_default(self):
        responses = [self._get_student(student) for student in self.students]

        self.assertEqual(
            [response.status_code for response in responses], [200, 200, 200]
        )

    def test_cache_failure_fails_closed_with_service_unavailable(self):
        with patch.object(cache, "add", side_effect=RuntimeError("cache unavailable")):
            response = self._get_student(self.students[0])

        self.assertEqual(response.status_code, 503)
        self.assertContains(response, "temporarily unavailable", status_code=503)
        self.assertContains(
            response, "Profile access is temporarily unavailable", status_code=503
        )
        self.assertNotContains(response, "Student profile", status_code=503)


@override_settings(**RATE_LIMIT_SETTINGS)
class StudentProfileRateLimitSlotTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user_id = 42
        self.organization_id = 7

    def test_organization_ids_have_independent_budgets(self):
        for _ in range(2):
            self.assertTrue(
                check_profile_rate_limit(self.user_id, self.organization_id)[0]
            )

        with self.assertLogs(
            "programs.utils.profile_rate_limit", level=logging.WARNING
        ):
            self.assertFalse(
                check_profile_rate_limit(self.user_id, self.organization_id)[0]
            )
        self.assertTrue(
            check_profile_rate_limit(self.user_id, self.organization_id + 1)[0]
        )

    @override_settings(
        PROFILE_RATE_LIMIT_COUNT=1,
        PROFILE_RATE_LIMIT_WINDOW_SECONDS=1,
    )
    def test_expired_slot_reopens_after_its_rolling_window(self):
        self.assertEqual(
            check_profile_rate_limit(self.user_id, self.organization_id),
            (True, 0),
        )
        with self.assertLogs(
            "programs.utils.profile_rate_limit", level=logging.WARNING
        ):
            allowed, retry_after = check_profile_rate_limit(
                self.user_id, self.organization_id
            )
        self.assertFalse(allowed)
        self.assertGreaterEqual(retry_after, 1)

        time.sleep(1.1)

        self.assertEqual(
            check_profile_rate_limit(self.user_id, self.organization_id),
            (True, 0),
        )

    def test_concurrent_requests_claim_no_more_than_the_limit(self):
        barrier = threading.Barrier(8)

        def claim_slot(_):
            barrier.wait(timeout=5)
            return check_profile_rate_limit(self.user_id, self.organization_id)[0]

        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(claim_slot, range(8)))

        self.assertEqual(sum(results), RATE_LIMIT_SETTINGS["PROFILE_RATE_LIMIT_COUNT"])
