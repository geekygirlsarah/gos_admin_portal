from datetime import timedelta
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import MagicMock

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from audit.events import AuditEvent
from audit.models import AuditLog
from audit.service import log_event
from organizations.models import Organization, UserOrganizationMembership
from programs.models import Adult, AdultStudentRelationship, Student


class OrganizationAuditLogTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.get(slug="gos")
        self.other = Organization.objects.create(name="Other", slug="audit-other")
        self.lead = get_user_model().objects.create_user("audit-lead", is_staff=True)
        UserOrganizationMembership.objects.create(
            user=self.lead, organization=self.org, role="LeadMentor", is_active=True
        )
        self.local = AuditLog.objects.create(
            organization=self.org,
            event=AuditEvent.CONTACT_INFO_UPDATED,
            resource_type="Student",
            resource_id="1",
            resource_repr="Local record",
            notes="Local detail",
        )
        self.foreign = AuditLog.objects.create(
            organization=self.other,
            event=AuditEvent.CONTACT_INFO_UPDATED,
            resource_type="Student",
            resource_id="2",
            resource_repr="Foreign record",
            notes="Foreign detail",
        )
        AuditLog.objects.create(
            event=AuditEvent.CONTACT_INFO_UPDATED,
            resource_type="Student",
            resource_id="3",
            resource_repr="Unattributed record",
        )
        self.client.force_login(self.lead)
        self.url = reverse("audit:organization_log")

    def test_only_local_records_are_visible_and_search_cannot_bypass_scope(self):
        response = self.client.get(self.url)
        self.assertContains(response, "Local record")
        self.assertIn("no-store", response["Cache-Control"])
        self.assertNotContains(response, "Foreign record")
        self.assertNotContains(response, "Unattributed record")
        response = self.client.get(self.url, {"search": "Foreign"})
        self.assertNotContains(response, "Foreign record")
        self.assertEqual(list(response.context["logs"]), [])

    def test_filters_and_invalid_input(self):
        response = self.client.get(
            self.url,
            {
                "event": AuditEvent.CONTACT_INFO_UPDATED,
                "outcome": "success",
                "search": "Local",
                "date_from": timezone.localdate().isoformat(),
                "date_to": timezone.localdate().isoformat(),
            },
        )
        self.assertEqual(list(response.context["logs"]), [self.local])
        response = self.client.get(
            self.url,
            {"date_from": (timezone.localdate() + timedelta(days=1)).isoformat()},
        )
        self.assertNotContains(response, "Local record")
        response = self.client.get(self.url, {"date_from": "not-a-date"})
        self.assertContains(response, "Enter a valid date")
        self.assertEqual(list(response.context["logs"]), [])
        response = self.client.get(self.url, {"event": AuditEvent.USER_LOGOUT})
        self.assertNotContains(response, "Local record")
        response = self.client.get(self.url, {"outcome": "failure"})
        self.assertNotContains(response, "Local record")
        response = self.client.get(
            self.url, {"date_from": "2026-02-02", "date_to": "2026-02-01"}
        )
        self.assertContains(response, "Choose a date on or after")

    def test_mentor_and_foreign_lead_are_denied(self):
        mentor = get_user_model().objects.create_user("audit-mentor")
        UserOrganizationMembership.objects.create(
            user=mentor, organization=self.org, role="Mentor", is_active=True
        )
        self.client.force_login(mentor)
        self.assertEqual(self.client.get(self.url).status_code, 302)
        foreign_lead = get_user_model().objects.create_user("audit-foreign")
        UserOrganizationMembership.objects.create(
            user=foreign_lead,
            organization=self.other,
            role="LeadMentor",
            is_active=True,
        )
        self.client.force_login(foreign_lead)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_platform_superuser_sees_only_current_organization(self):
        admin = get_user_model().objects.create_superuser("audit-platform")
        self.client.force_login(admin)
        response = self.client.get(self.url)
        self.assertContains(response, "Local record")
        self.assertNotContains(response, "Foreign record")

    @override_settings(MOMENTUM_ROOT_DOMAIN="momentumteam.app", ALLOWED_HOSTS=["*"])
    def test_foreign_lead_sees_only_foreign_organization_on_its_host(self):
        self.other.subdomain = "audit-other"
        self.other.save(update_fields=["subdomain"])
        foreign_lead = get_user_model().objects.create_user("other-lead")
        UserOrganizationMembership.objects.create(
            user=foreign_lead,
            organization=self.other,
            role="LeadMentor",
            is_active=True,
        )
        self.client.force_login(foreign_lead)
        response = self.client.get(self.url, HTTP_HOST="audit-other.momentumteam.app")
        self.assertContains(response, "Foreign record")
        self.assertNotContains(response, "Local record")
        self.assertNotContains(response, "Unattributed record")

    def test_missing_organization_is_denied_even_for_superuser(self):
        from audit.views import OrganizationAuditLogView

        request = RequestFactory().get("/")
        request.organization = None
        request.user = get_user_model().objects.create_superuser("audit-platform")
        with self.assertRaises(PermissionDenied):
            OrganizationAuditLogView.as_view()(request)

    def test_global_admin_and_export_denied_to_lead(self):
        for path in ("/admin/audit/auditlog/", "/admin/audit/auditlog/export/"):
            self.assertEqual(self.client.get(path).status_code, 403)

    def test_menu_links_to_organization_page(self):
        response = self.client.get(reverse("program_list"))
        self.assertContains(response, f'href="{self.url}">Audit Logs')
        self.assertNotContains(response, 'href="/admin/audit/auditlog/"')

    def test_pagination_keeps_encoded_filters(self):
        for index in range(51):
            AuditLog.objects.create(
                organization=self.org,
                event=AuditEvent.CONTACT_INFO_UPDATED,
                resource_type="Student",
                resource_id=str(index + 10),
                resource_repr="Local & record",
            )
        response = self.client.get(self.url, {"search": "Local & record"})
        self.assertContains(response, "search=Local+%26+record&amp;page=2")
        response = self.client.get(self.url, {"search": "Local & record", "page": 2})
        self.assertEqual(len(response.context["logs"]), 1)

    def test_page_size_defaults_to_recent_50_and_respects_allowed_choices(self):
        AuditLog.objects.bulk_create(
            [
                AuditLog(
                    organization=self.org,
                    event=AuditEvent.CONTACT_INFO_UPDATED,
                    resource_type="Student",
                    resource_id=str(index),
                    resource_repr="Pagination match",
                )
                for index in range(501)
            ]
        )
        matching = list(
            AuditLog.objects.filter(
                organization=self.org, resource_repr="Pagination match"
            ).order_by("-timestamp", "-pk")
        )
        response = self.client.get(self.url, {"search": "Pagination match"})
        self.assertEqual(list(response.context["logs"]), matching[:50])
        self.assertEqual(response.context["page_obj"].paginator.per_page, 50)
        self.assertContains(
            response, '<option value="50" selected>50</option>', html=True
        )

        for size in (50, 100, 200, 500):
            with self.subTest(page_size=size):
                response = self.client.get(
                    self.url, {"search": "Pagination match", "page_size": size}
                )
                self.assertEqual(list(response.context["logs"]), matching[:size])
                self.assertContains(response, f"page_size={size}&amp;page=2")
                response = self.client.get(
                    self.url,
                    {"search": "Pagination match", "page_size": size, "page": 2},
                )
                self.assertEqual(
                    list(response.context["logs"]), matching[size : size * 2]
                )

    def test_page_size_rejects_unbounded_or_invalid_values(self):
        for value in ("all", "501", "-1", "0"):
            with self.subTest(page_size=value):
                response = self.client.get(self.url, {"page_size": value})
                self.assertContains(response, "Select a valid choice")
                self.assertEqual(list(response.context["logs"]), [])
                self.assertEqual(response.context["page_obj"].paginator.per_page, 50)

    def test_attribution_uses_request_and_rejects_mismatched_resource(self):
        request = RequestFactory().get("/")
        request.organization = self.org
        request.user = self.lead
        local_student = Student.objects.create(
            organization=self.org, legal_first_name="Local", last_name="Student"
        )
        foreign_student = Student.objects.create(
            organization=self.other, legal_first_name="Foreign", last_name="Student"
        )
        local = log_event(
            event=AuditEvent.SENSITIVE_DATA_VIEW,
            resource=local_student,
            request=request,
        )
        foreign = log_event(
            event=AuditEvent.SENSITIVE_DATA_VIEW,
            resource=foreign_student,
            request=request,
        )
        self.assertEqual(local.organization_id, self.org.pk)
        self.assertIsNone(foreign.organization_id)

    def test_missing_tenant_context_is_not_attributed(self):
        entry = log_event(
            event=AuditEvent.ROLE_CHANGED,
            resource=self.lead,
            request=SimpleNamespace(user=self.lead, META={}),
        )
        self.assertIsNone(entry.organization_id)

    def test_explicit_organization_must_match_request(self):
        request = RequestFactory().get("/")
        request.organization = self.org
        request.user = self.lead
        entry = log_event(
            event=AuditEvent.ROLE_CHANGED,
            resource=self.lead,
            request=request,
            organization=self.other,
        )
        self.assertIsNone(entry.organization_id)

    def test_foreign_user_is_not_attributed_to_request_organization(self):
        foreign_user = get_user_model().objects.create_user("other-audit-user")
        UserOrganizationMembership.objects.create(
            user=foreign_user, organization=self.other, role="Mentor", is_active=True
        )
        request = RequestFactory().get("/")
        request.organization = self.org
        request.user = self.lead
        entry = log_event(
            event=AuditEvent.LOGIN_FAILED, resource=foreign_user, request=request
        )
        self.assertIsNone(entry.organization_id)

    def test_inconsistent_guardian_relationship_is_not_attributed(self):
        request = RequestFactory().get("/")
        request.organization = self.org
        request.user = self.lead
        adult = Adult.objects.create(
            organization=self.org, legal_first_name="Local", last_name="Adult"
        )
        student = Student.objects.create(
            organization=self.other, legal_first_name="Foreign", last_name="Student"
        )
        relationship = AdultStudentRelationship.objects.create(
            organization=self.org, adult=adult, student=student
        )
        entry = log_event(
            event=AuditEvent.GUARDIAN_REMOVED, resource=relationship, request=request
        )
        self.assertIsNone(entry.organization_id)

    def test_page_does_not_expose_session_or_snapshots(self):
        AuditLog.objects.create(
            organization=self.org,
            event=AuditEvent.ROLE_CHANGED,
            resource_type="User",
            resource_id="4",
            session_id="private-session-value",
            before={"private_value": "private-snapshot-value"},
        )
        response = self.client.get(self.url)
        self.assertNotContains(response, "private-session-value")
        self.assertNotContains(response, "private-snapshot-value")


class AuditBackfillTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.get(slug="gos")
        self.user = get_user_model().objects.create_user("historical-member")
        UserOrganizationMembership.objects.create(
            user=self.user, organization=self.org, role="Mentor", is_active=True
        )
        self.migration = import_module(
            "audit.migrations.0011_backfill_gos_audit_organization"
        )

    def create_log(self, event, **kwargs):
        return AuditLog.objects.create(
            event=event, resource_type="User", resource_id=str(self.user.pk), **kwargs
        )

    def backfill(self):
        historical_apps = (
            MigrationExecutor(connection)
            .loader.project_state([("audit", "0011_backfill_gos_audit_organization")])
            .apps
        )
        self.migration.backfill_first_organization(
            historical_apps, SimpleNamespace(connection=connection)
        )

    def test_single_organization_backfill_is_selective_and_idempotent(self):
        ordinary = self.create_log(AuditEvent.USER_LOGIN)
        platform = self.create_log(AuditEvent.USER_IMPERSONATED)
        matching = self.create_log(
            AuditEvent.SECURITY_ALERT, after={"organization_id": self.org.pk}
        )
        conflicting = self.create_log(
            AuditEvent.SECURITY_ALERT, after={"organization_id": self.org.pk + 100}
        )
        missing = self.create_log(AuditEvent.SECURITY_ALERT)
        outsider = get_user_model().objects.create_user("historical-outsider")
        unowned = AuditLog.objects.create(
            event=AuditEvent.USER_LOGIN,
            resource_type="User",
            resource_id=str(outsider.pk),
        )
        self.backfill()
        self.backfill()
        for entry in (ordinary, matching):
            entry.refresh_from_db()
            self.assertEqual(entry.organization_id, self.org.pk)
        for entry in (platform, conflicting, missing, unowned):
            entry.refresh_from_db()
            self.assertIsNone(entry.organization_id)

    def test_single_organization_does_not_require_gos_slug(self):
        Organization.objects.filter(pk=self.org.pk).update(slug="another-team")
        entry = self.create_log(AuditEvent.USER_LOGIN)
        self.backfill()
        entry.refresh_from_db()
        self.assertEqual(entry.organization_id, self.org.pk)

    def test_multiple_organizations_are_never_guessed(self):
        Organization.objects.create(name="Other", slug="backfill-other")
        entry = self.create_log(AuditEvent.USER_LOGIN)
        with self.assertLogs(self.migration.logger, level="WARNING") as logs:
            self.backfill()
        self.assertIn("Audit history was not backfilled", logs.output[0])
        entry.refresh_from_db()
        self.assertIsNone(entry.organization_id)

    def test_missing_organization_is_reported(self):
        fake_apps = MagicMock()
        organizations = fake_apps.get_model.return_value.objects.using.return_value
        organizations.order_by.return_value.first.return_value = None
        with self.assertLogs(self.migration.logger, level="WARNING") as logs:
            self.migration.backfill_first_organization(
                fake_apps, SimpleNamespace(connection=connection)
            )
        self.assertIn("Audit history was not backfilled", logs.output[0])
