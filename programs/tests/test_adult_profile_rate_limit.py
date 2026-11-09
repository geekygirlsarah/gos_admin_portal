import logging
import time
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from audit.events import AuditEvent
from audit.models import AuditLog
from organizations.models import Organization, UserOrganizationMembership
from programs.models import (
    Adult,
    AdultStudentRelationship,
    Enrollment,
    Program,
    RolePermission,
    Student,
)


@override_settings(
    TESTING=True,
    PROFILE_RATE_LIMIT_ENABLED=True,
    PROFILE_RATE_LIMIT_COUNT=2,
    PROFILE_RATE_LIMIT_WINDOW_SECONDS=60,
)
class AdultProfileRateLimitTests(TestCase):
    def setUp(self):
        cache.clear()
        self.organization = Organization.objects.get(slug="gos")
        self.mentor = self.create_user("mentor", "Mentor")
        RolePermission.objects.update_or_create(
            organization=self.organization,
            role="Mentor",
            section="adult_info",
            defaults={"can_read": True},
        )
        program = Program.objects.create(
            organization=self.organization, name="Active program", active=True
        )
        self.student = Student.objects.create(
            organization=self.organization, legal_first_name="Student", last_name="Test"
        )
        Enrollment.objects.create(
            organization=self.organization, student=self.student, program=program
        )
        self.adults = []
        for index in range(3):
            adult = Adult.objects.create(
                organization=self.organization,
                legal_first_name=f"Parent{index}",
                last_name="Test",
                is_parent=True,
            )
            AdultStudentRelationship.objects.create(
                organization=self.organization, adult=adult, student=self.student
            )
            self.adults.append(adult)
        self.client.force_login(self.mentor)

    def create_user(self, username, role, *, is_superuser=False):
        user = User.objects.create_user(username=username, is_superuser=is_superuser)
        if not is_superuser:
            group, _ = Group.objects.get_or_create(name=role)
            user.groups.add(group)
            UserOrganizationMembership.objects.create(
                user=user, organization=self.organization, role=role, is_active=True
            )
        return user

    def get_adult(self, adult, **kwargs):
        return self.client.get(reverse("adult_detail", args=[adult.pk]), **kwargs)

    def test_limit_returns_cooldown_and_only_successful_views_are_audited(self):
        for adult in self.adults[:2]:
            self.assertEqual(self.get_adult(adult).status_code, 200)
        with self.assertLogs(
            "programs.utils.profile_rate_limit", level=logging.WARNING
        ) as captured:
            response = self.get_adult(self.adults[2])
        self.assertEqual(response.status_code, 429)
        self.assertGreaterEqual(int(response["Retry-After"]), 1)
        self.assertLessEqual(int(response["Retry-After"]), 60)
        self.assertContains(response, "adult directory", status_code=429)
        self.assertContains(response, "wait", status_code=429)
        self.assertTrue(any("adult" in line.lower() for line in captured.output))
        self.assertEqual(
            AuditLog.objects.filter(
                event=AuditEvent.SENSITIVE_DATA_VIEW,
                actor=self.mentor,
                resource_type="Adult",
            ).count(),
            2,
        )

    def test_student_and_adult_budgets_are_independent(self):
        student_url = reverse("student_detail", args=[self.student.pk])
        self.assertEqual(self.client.get(student_url).status_code, 200)
        self.assertEqual(self.client.get(student_url).status_code, 200)
        self.assertEqual(self.get_adult(self.adults[0]).status_code, 200)
        self.assertEqual(self.get_adult(self.adults[1]).status_code, 200)
        self.assertEqual(self.get_adult(self.adults[2]).status_code, 429)
        self.assertEqual(self.client.get(student_url).status_code, 429)

    def test_denied_and_foreign_adults_do_not_consume_budget(self):
        foreign_org = Organization.objects.create(name="Foreign", slug="foreign")
        foreign = Adult.objects.create(
            organization=foreign_org, legal_first_name="Foreign", last_name="Adult"
        )
        denied = Adult.objects.create(
            organization=self.organization, legal_first_name="Denied", last_name="Adult"
        )
        for adult in (foreign, denied):
            response = self.get_adult(adult)
            self.assertEqual(response.status_code, 302)
            self.assertNotContains(response, adult.legal_first_name, status_code=302)
        self.assertEqual(self.get_adult(self.adults[0]).status_code, 200)
        self.assertEqual(self.get_adult(self.adults[1]).status_code, 200)
        self.assertEqual(self.get_adult(self.adults[2]).status_code, 429)
        self.assertEqual(self.get_adult(foreign).status_code, 302)

    def test_lead_mentor_and_superuser_are_exempt(self):
        for username, role, is_superuser in (
            ("lead", "LeadMentor", False),
            ("admin", "LeadMentor", True),
        ):
            with self.subTest(role=username):
                self.client.force_login(
                    self.create_user(username, role, is_superuser=is_superuser)
                )
                self.assertEqual(
                    [self.get_adult(adult).status_code for adult in self.adults],
                    [200, 200, 200],
                )

    @override_settings(PROFILE_RATE_LIMIT_ENABLED=False)
    def test_disabled_limiter_allows_requests(self):
        self.assertEqual(
            [self.get_adult(adult).status_code for adult in self.adults],
            [200, 200, 200],
        )

    def test_cache_failure_is_unavailable_without_audit(self):
        with patch.object(cache, "add", side_effect=RuntimeError("cache unavailable")):
            response = self.get_adult(self.adults[0])
        self.assertEqual(response.status_code, 503)
        self.assertContains(response, "temporarily unavailable", status_code=503)
        self.assertContains(
            response, "Profile access is temporarily unavailable", status_code=503
        )
        self.assertNotContains(response, "Adult profile", status_code=503)
        self.assertFalse(
            AuditLog.objects.filter(
                event=AuditEvent.SENSITIVE_DATA_VIEW,
                actor=self.mentor,
                resource_type="Adult",
            ).exists()
        )

    def test_other_mentors_have_independent_budgets(self):
        for adult in self.adults[:2]:
            self.assertEqual(self.get_adult(adult).status_code, 200)
        other = self.create_user("other", "Mentor")
        self.client.force_login(other)
        self.assertEqual(self.get_adult(self.adults[0]).status_code, 200)

    def test_same_mentor_has_separate_organization_budgets(self):
        other_org = Organization.objects.create(
            name="Other", slug="adult-throttle-other", subdomain="adult-throttle-other"
        )
        UserOrganizationMembership.objects.create(
            user=self.mentor, organization=other_org, role="Mentor", is_active=True
        )
        program = Program.objects.create(organization=other_org, name="Other program")
        student = Student.objects.create(
            organization=other_org, legal_first_name="Other", last_name="Student"
        )
        Enrollment.objects.create(
            organization=other_org, student=student, program=program
        )
        adult = Adult.objects.create(
            organization=other_org,
            legal_first_name="Other",
            last_name="Parent",
            is_parent=True,
        )
        AdultStudentRelationship.objects.create(
            organization=other_org, student=student, adult=adult
        )
        for local_adult in self.adults[:2]:
            self.assertEqual(self.get_adult(local_adult).status_code, 200)
        response = self.get_adult(adult, HTTP_HOST="adult-throttle-other.localhost")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.get_adult(self.adults[2]).status_code, 429)

    def test_parent_own_profile_is_not_throttled(self):
        parent = self.create_user("parent", "Parent")
        self.adults[0].user = parent
        self.adults[0].save(update_fields=["user"])
        self.client.force_login(parent)
        self.assertEqual(
            [self.get_adult(self.adults[0]).status_code for _ in range(3)],
            [200, 200, 200],
        )

    @override_settings(
        PROFILE_RATE_LIMIT_COUNT=1,
        PROFILE_RATE_LIMIT_WINDOW_SECONDS=1,
    )
    def test_expired_adult_slot_reopens(self):
        self.assertEqual(self.get_adult(self.adults[0]).status_code, 200)
        self.assertEqual(self.get_adult(self.adults[1]).status_code, 429)
        time.sleep(1.1)
        self.assertEqual(self.get_adult(self.adults[1]).status_code, 200)
