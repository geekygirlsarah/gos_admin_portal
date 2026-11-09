from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from audit.events import AuditEvent
from audit.models import AuditLog
from audit.profile_alerts import check_profile_access_anomaly
from organizations.models import Organization, UserOrganizationMembership
from organizations.utils import get_default_organization
from programs.models import Adult, Student

User = get_user_model()


ALERT_SETTINGS = {
    "PROFILE_ACCESS_ALERT_THRESHOLD": 2,
    "PROFILE_ACCESS_ALERT_WINDOW_SECONDS": 1800,
    "PROFILE_ACCESS_ALERT_COOLDOWN_SECONDS": 21600,
    "ADMIN_EMAILS": ["platform-admin@example.com"],
}


@override_settings(**ALERT_SETTINGS)
class ProfileAccessAlertTests(TestCase):
    def setUp(self):
        cache.clear()
        organization = get_default_organization()
        if organization is None:
            raise AssertionError("The configured default organization is required")
        self.organization = organization
        self.mentor = self._create_member("alert_mentor", "Mentor")
        self.lead = self._create_member("alert_lead", "LeadMentor")
        self.students = [
            Student.objects.create(
                organization=self.organization,
                legal_first_name=f"Student{index}",
                last_name="AlertTest",
            )
            for index in range(1, 5)
        ]

    def _create_member(self, username, role):
        user = User.objects.create(username=username, email=f"{username}@example.com")
        UserOrganizationMembership.objects.create(
            user=user, organization=self.organization, role=role, is_active=True
        )
        return user

    @staticmethod
    def _audit_student_view(actor, student):
        return AuditLog.objects.create(
            event=AuditEvent.SENSITIVE_DATA_VIEW,
            actor=actor,
            resource_type="Student",
            resource_id=str(student.pk),
            resource_repr=str(student),
        )

    def test_distinct_student_threshold_sends_one_tenant_scoped_alert(self):
        foreign_organization = Organization.objects.create(
            name="Foreign Recipient Organization", slug="foreign-alert-recipient"
        )
        foreign_lead = User.objects.create(
            username="foreign_alert_lead", email="foreign-lead@example.com"
        )
        UserOrganizationMembership.objects.create(
            user=foreign_lead,
            organization=foreign_organization,
            role="LeadMentor",
            is_active=True,
        )
        self.client.force_login(self.mentor)
        with patch("audit.profile_alerts.send_templated_notification") as send_email:
            with self.captureOnCommitCallbacks(execute=True):
                for student in (
                    self.students[0],
                    self.students[0],
                    *self.students[1:4],
                ):
                    response = self.client.get(
                        reverse("student_detail", kwargs={"pk": student.pk})
                    )
                    self.assertEqual(response.status_code, 200)

        alerts = AuditLog.objects.filter(
            event=AuditEvent.SECURITY_ALERT, actor=self.mentor
        )
        self.assertEqual(alerts.count(), 1)
        alert = alerts.get()
        self.assertEqual(alert.after["organization_id"], self.organization.pk)
        self.assertEqual(alert.after["distinct_student_count"], 3)
        self.assertEqual(alert.after["reason"], "profile_views")
        send_email.assert_called_once()
        kwargs = send_email.call_args.kwargs
        self.assertEqual(
            set(kwargs["recipient_list"]),
            {"platform-admin@example.com", "alert_lead@example.com"},
        )
        self.assertNotIn("foreign-lead@example.com", kwargs["recipient_list"])
        self.assertEqual(kwargs["organization"], self.organization)
        self.assertEqual(kwargs["context"]["distinct_student_count"], 3)
        self.assertEqual(kwargs["context"]["actor_email"], "alert_mentor@example.com")

    def test_foreign_student_and_adult_views_do_not_count_toward_tenant_threshold(self):
        foreign_organization = Organization.objects.create(
            name="Foreign Organization", slug="foreign-alert-test"
        )
        foreign_students = [
            Student.objects.create(
                organization=foreign_organization,
                legal_first_name=f"Foreign{index}",
                last_name="AlertTest",
            )
            for index in range(1, 4)
        ]
        for student in foreign_students:
            self._audit_student_view(self.mentor, student)
        adult = Adult.objects.create(
            organization=self.organization,
            legal_first_name="Adult",
            last_name="AlertTest",
        )
        AuditLog.objects.create(
            event=AuditEvent.SENSITIVE_DATA_VIEW,
            actor=self.mentor,
            resource_type="Adult",
            resource_id=str(adult.pk),
            resource_repr=str(adult),
        )
        for student in self.students[:2]:
            self._audit_student_view(self.mentor, student)

        self.assertFalse(check_profile_access_anomaly(self.mentor, self.organization))
        self._audit_student_view(self.mentor, self.students[2])
        self.assertTrue(check_profile_access_anomaly(self.mentor, self.organization))
        self.assertEqual(
            AuditLog.objects.filter(event=AuditEvent.SECURITY_ALERT).count(), 1
        )

    def test_lead_mentor_is_exempt_from_profile_access_alerts(self):
        for student in self.students[:3]:
            self._audit_student_view(self.lead, student)

        self.assertFalse(check_profile_access_anomaly(self.lead, self.organization))

        superuser = User.objects.create(
            username="alert_superuser",
            email="superuser@example.com",
            is_staff=True,
            is_superuser=True,
        )
        for student in self.students[:3]:
            self._audit_student_view(superuser, student)
        self.assertFalse(check_profile_access_anomaly(superuser, self.organization))
        self.assertFalse(
            AuditLog.objects.filter(event=AuditEvent.SECURITY_ALERT).exists()
        )

    def test_views_older_than_the_rolling_window_do_not_count(self):
        old_time = timezone.now() - timedelta(minutes=31)
        with patch("audit.profile_alerts.timezone.now", return_value=old_time):
            for student in self.students[:3]:
                self._audit_student_view(self.mentor, student)

        self.assertFalse(check_profile_access_anomaly(self.mentor, self.organization))
        for student in self.students[:3]:
            self._audit_student_view(self.mentor, student)
        self.assertTrue(check_profile_access_anomaly(self.mentor, self.organization))

    def test_cache_failure_does_not_break_successful_profile_views(self):
        self.client.force_login(self.mentor)
        with patch.object(cache, "add", side_effect=RuntimeError):
            for student in self.students[:3]:
                response = self.client.get(
                    reverse("student_detail", kwargs={"pk": student.pk})
                )
                self.assertEqual(response.status_code, 200)
        self.assertFalse(
            AuditLog.objects.filter(event=AuditEvent.SECURITY_ALERT).exists()
        )

    def test_email_failure_does_not_disrupt_profile_or_lose_the_audit_alert(self):
        self.client.force_login(self.mentor)
        with patch(
            "audit.profile_alerts.send_templated_notification",
            side_effect=RuntimeError("mail backend unavailable"),
        ) as send_email:
            with self.captureOnCommitCallbacks(execute=True):
                for student in self.students[:3]:
                    response = self.client.get(
                        reverse("student_detail", kwargs={"pk": student.pk})
                    )
                    self.assertEqual(response.status_code, 200)

        self.assertEqual(send_email.call_count, 1)
        self.assertEqual(
            AuditLog.objects.filter(event=AuditEvent.SECURITY_ALERT).count(), 1
        )

    def test_authorized_429_emits_debounced_alert(self):
        cache.clear()
        self.client.force_login(self.mentor)
        with override_settings(
            PROFILE_RATE_LIMIT_ENABLED=True,
            PROFILE_RATE_LIMIT_COUNT=1,
            PROFILE_RATE_LIMIT_WINDOW_SECONDS=60,
        ):
            with patch(
                "audit.profile_alerts.send_templated_notification"
            ) as send_email:
                with self.captureOnCommitCallbacks(execute=True):
                    first_response = self.client.get(
                        reverse("student_detail", kwargs={"pk": self.students[0].pk})
                    )
                    limited_response = self.client.get(
                        reverse("student_detail", kwargs={"pk": self.students[1].pk})
                    )
                    another_limited_response = self.client.get(
                        reverse("student_detail", kwargs={"pk": self.students[2].pk})
                    )

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(limited_response.status_code, 429)
        self.assertEqual(another_limited_response.status_code, 429)
        alert = AuditLog.objects.get(event=AuditEvent.SECURITY_ALERT)
        self.assertEqual(alert.after["reason"], "rate_limit")
        self.assertEqual(
            AuditLog.objects.filter(event=AuditEvent.SECURITY_ALERT).count(), 1
        )
        send_email.assert_called_once()


@override_settings(**ALERT_SETTINGS)
class ProfileAlertAdminVisibilityTests(TestCase):
    def setUp(self):
        organization = get_default_organization()
        if organization is None:
            raise AssertionError("The configured default organization is required")
        self.organization = organization
        self.foreign_organization = Organization.objects.create(
            name="Foreign Organization", slug="foreign-alert-admin-test"
        )
        self.lead = User.objects.create(
            username="alert_admin_lead", email="lead@example.com", is_staff=True
        )
        UserOrganizationMembership.objects.create(
            user=self.lead,
            organization=self.organization,
            role="LeadMentor",
            is_active=True,
        )
        self.local_alert = self._create_alert(self.organization)
        self.foreign_alert = self._create_alert(self.foreign_organization)

    def _create_alert(self, organization):
        return AuditLog.objects.create(
            event=AuditEvent.SECURITY_ALERT,
            actor=self.lead,
            resource_type="User",
            resource_id=str(self.lead.pk),
            after={"organization_id": organization.pk, "reason": "profile_views"},
        )

    def test_non_superuser_lead_only_sees_alerts_for_current_organization(self):
        from django.contrib import admin

        request = RequestFactory().get("/admin/audit/auditlog/")
        request.user = self.lead
        setattr(request, "organization", self.organization)
        model_admin = admin.site._registry[AuditLog]

        visible_ids = set(
            model_admin.get_queryset(request).values_list("pk", flat=True)
        )

        self.assertIn(self.local_alert.pk, visible_ids)
        self.assertNotIn(self.foreign_alert.pk, visible_ids)

    def test_alerts_are_hidden_when_tenant_context_is_missing(self):
        from django.contrib import admin

        request = RequestFactory().get("/admin/audit/auditlog/")
        request.user = self.lead
        model_admin = admin.site._registry[AuditLog]

        visible_alerts = model_admin.get_queryset(request).filter(
            event=AuditEvent.SECURITY_ALERT
        )

        self.assertFalse(visible_alerts.exists())
