import json
import uuid

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.db.models.signals import post_init
from django.test import TestCase, override_settings
from django.urls import reverse

from applications.models import Application
from GoSAdminPortal.test_runner import _assign_default_test_org
from organizations.models import Organization, UserOrganizationMembership
from programs.models import (
    Adult,
    EmailDeliveryEvent,
    EmailDeliveryQuarantine,
    Program,
    Student,
    TrustedOutgoingEmailRecipient,
)
from programs.utils.tosend_webhooks import _email_fingerprint

_FILE_ENCRYPTION_KEY = Fernet.generate_key().decode()


@override_settings(
    ALLOWED_HOSTS=[".localhost", "testserver"],
    FILE_ENCRYPTION_KEY=_FILE_ENCRYPTION_KEY,
    MOMENTUM_ROOT_DOMAIN="localhost",
)
class EmailDeliveryTenantIsolationTests(TestCase):
    tenant_models = (Adult, Application, Program, Student)

    def setUp(self):
        self.disconnected_models = []
        for model in self.tenant_models:
            if post_init.disconnect(
                receiver=_assign_default_test_org,
                sender=model,
                dispatch_uid=f"default-test-org-{model._meta.label}",
            ):
                self.disconnected_models.append(model)
        self.addCleanup(self._restore_default_organization_hook)

        self.alpha = Organization.objects.create(
            name="Delivery Alpha", slug="delivery-alpha", subdomain="delivery-alpha"
        )
        self.beta = Organization.objects.create(
            name="Delivery Beta", slug="delivery-beta", subdomain="delivery-beta"
        )
        self.alpha_host = "delivery-alpha.localhost"
        self.beta_host = "delivery-beta.localhost"
        self.alpha_program = Program.objects.create(
            organization=self.alpha, name="Alpha Program", active=True
        )
        self.beta_program = Program.objects.create(
            organization=self.beta, name="Beta Confidential Program", active=True
        )
        self.alpha_student = Student.objects.create(
            organization=self.alpha,
            legal_first_name="Avery",
            last_name="Alpha",
            personal_email="shared@example.test",
        )
        self.beta_student = Student.objects.create(
            organization=self.beta,
            legal_first_name="Blair",
            last_name="Beta",
            personal_email="shared@example.test",
        )
        self.alpha_adult = Adult.objects.create(
            organization=self.alpha,
            legal_first_name="Pat",
            last_name="Alpha Parent",
            personal_email="shared@example.test",
            is_parent=True,
        )
        self.lead_user = get_user_model().objects.create_user(
            username="delivery-alpha-lead",
            email="alpha-lead@example.test",
            password="password123",
        )  # nosec B106
        UserOrganizationMembership.objects.create(
            user=self.lead_user,
            organization=self.alpha,
            role="LeadMentor",
            is_active=True,
        )
        UserOrganizationMembership.objects.create(
            user=self.lead_user,
            organization=self.beta,
            role="Mentor",
            is_active=True,
        )
        self.beta_lead_user = get_user_model().objects.create_user(
            username="delivery-beta-lead",
            email="beta-lead@example.test",
            password="password123",
        )  # nosec B106
        UserOrganizationMembership.objects.create(
            user=self.beta_lead_user,
            organization=self.beta,
            role="LeadMentor",
            is_active=True,
        )

    def _restore_default_organization_hook(self):
        for model in self.disconnected_models:
            post_init.connect(
                receiver=_assign_default_test_org,
                sender=model,
                dispatch_uid=f"default-test-org-{model._meta.label}",
            )

    def _event(self, organization, recipient, **kwargs):
        is_verified = kwargs.pop("is_verified", False)
        if is_verified:
            message_id = f"tenant-test-message-{uuid.uuid4().hex}"
            recipient_fingerprint = _email_fingerprint(recipient)
            trusted_mapping = TrustedOutgoingEmailRecipient.objects.create(
                organization=organization,
                provider_message_id=message_id,
                recipient_email=recipient,
                recipient_fingerprint=recipient_fingerprint,
            )
            kwargs.update(
                {
                    "is_verified": True,
                    "message_id": message_id,
                    "recipient_fingerprint": recipient_fingerprint,
                    "trusted_mapping": trusted_mapping,
                }
            )
        return EmailDeliveryEvent.objects.create(
            organization=organization,
            recipient_email=recipient,
            event_type=EmailDeliveryEvent.EVENT_BOUNCED,
            reason="Delivery diagnostic for testing",
            **kwargs,
        )

    def test_unverified_historical_event_is_hidden_and_cannot_be_resolved(self):
        event = self._event(
            self.alpha,
            "legacy.private@example.test",
            student=self.alpha_student,
        )
        adult_event = self._event(
            self.alpha,
            "legacy-parent.private@example.test",
            adult=self.alpha_adult,
        )
        self.assertFalse(event.is_verified)
        self.assertFalse(self.alpha_student.has_email_delivery_issue)
        self.assertFalse(self.alpha_adult.has_email_delivery_issue)
        self.client.force_login(self.lead_user)

        detail = self.client.get(
            reverse("student_detail", args=[self.alpha_student.pk]),
            HTTP_HOST=self.alpha_host,
        )
        adult_detail = self.client.get(
            reverse("adult_detail", args=[self.alpha_adult.pk]),
            HTTP_HOST=self.alpha_host,
        )
        student_list = self.client.get(
            reverse("student_list"), HTTP_HOST=self.alpha_host
        )
        adult_list = self.client.get(reverse("adult_list"), HTTP_HOST=self.alpha_host)
        issues = self.client.get(
            reverse("email_delivery_issues_list"), HTTP_HOST=self.alpha_host
        )
        dashboard = self.client.get(
            reverse("profile_dashboard"), HTTP_HOST=self.alpha_host
        )
        resolution = self.client.post(
            reverse("email_delivery_issue_resolve", args=[event.pk]),
            HTTP_HOST=self.alpha_host,
        )

        self.assertEqual(detail.status_code, 200)
        self.assertNotContains(detail, "Email Delivery Warning")
        self.assertNotContains(detail, "Delivery diagnostic for testing")
        self.assertEqual(adult_detail.status_code, 200)
        self.assertNotContains(adult_detail, "Email Delivery Warning")
        self.assertNotContains(student_list, "legacy.private@example.test")
        self.assertNotContains(adult_list, "legacy-parent.private@example.test")
        listed_student = next(
            item
            for item in student_list.context["students"]
            if item.pk == self.alpha_student.pk
        )
        listed_adult = next(
            item
            for item in adult_list.context["adults"]
            if item.pk == self.alpha_adult.pk
        )
        self.assertFalse(listed_student.has_email_delivery_issue)
        self.assertFalse(listed_adult.has_email_delivery_issue)
        self.assertEqual(issues.status_code, 200)
        self.assertNotContains(issues, "legacy.private@example.test")
        self.assertNotContains(issues, "legacy-parent.private@example.test")
        self.assertNotContains(issues, "Delivery diagnostic for testing")
        self.assertEqual(
            dashboard.context["lead_stats"]["pending_email_issues_count"], 0
        )
        self.assertEqual(resolution.status_code, 302)
        event.refresh_from_db()
        adult_event.refresh_from_db()
        self.assertFalse(event.resolved)
        self.assertFalse(adult_event.resolved)

    def test_only_verified_consistent_local_events_appear_in_tenant_surfaces(self):
        local_event = self._event(
            self.alpha,
            "local-bounce@example.test",
            student=self.alpha_student,
            is_verified=True,
        )
        foreign_event = self._event(
            self.beta,
            "foreign-bounce@example.test",
            student=self.beta_student,
            is_verified=True,
        )
        self._event(
            self.alpha,
            "mismatched-student@example.test",
            student=self.beta_student,
            is_verified=True,
        )
        self._event(
            self.alpha,
            "ambiguous-links@example.test",
            student=self.alpha_student,
            adult=self.alpha_adult,
            is_verified=True,
        )
        cross_linked_event = self._event(
            self.beta,
            "cross-linked-private@example.test",
            student=self.alpha_student,
            is_verified=True,
        )
        self.client.force_login(self.lead_user)

        response = self.client.get(
            reverse("email_delivery_issues_list"), HTTP_HOST=self.alpha_host
        )
        student_list = self.client.get(
            reverse("student_list"), HTTP_HOST=self.alpha_host
        )
        dashboard = self.client.get(
            reverse("profile_dashboard"), HTTP_HOST=self.alpha_host
        )

        self.assertContains(response, "local-bounce@example.test")
        self.assertNotContains(response, "foreign-bounce@example.test")
        self.assertNotContains(response, "mismatched-student@example.test")
        self.assertNotContains(response, "ambiguous-links@example.test")
        listed_student = next(
            item
            for item in student_list.context["students"]
            if item.pk == self.alpha_student.pk
        )
        self.assertEqual(list(listed_student.email_delivery_issues), [local_event])
        self.assertEqual(
            dashboard.context["lead_stats"]["pending_email_issues_count"], 1
        )
        self.assertEqual(
            list(
                EmailDeliveryEvent.objects.verified_for_organization(self.alpha)
                .filter(pk=local_event.pk)
                .values_list("pk", flat=True)
            ),
            [local_event.pk],
        )
        self.assertEqual(list(self.alpha_student.email_delivery_issues), [local_event])

        self.client.force_login(self.beta_lead_user)
        beta_response = self.client.get(
            reverse("email_delivery_issues_list"), HTTP_HOST=self.beta_host
        )
        self.assertContains(beta_response, "foreign-bounce@example.test")
        self.assertNotContains(beta_response, "local-bounce@example.test")
        self.assertNotContains(beta_response, "cross-linked-private@example.test")
        self.assertNotEqual(foreign_event.organization_id, local_event.organization_id)
        self.assertEqual(cross_linked_event.student_id, self.alpha_student.pk)

    def test_entity_resolution_is_verified_local_and_unambiguous(self):
        unverified = self._event(self.alpha, "shared@example.test")
        unverified.resolve_entities()
        self.assertIsNone(unverified.student_id)
        self.assertIsNone(unverified.adult_id)

        shared_local_identity = self._event(
            self.alpha, "shared@example.test", is_verified=True
        )
        shared_local_identity.resolve_entities()
        self.assertIsNone(shared_local_identity.student_id)
        self.assertIsNone(shared_local_identity.adult_id)
        self.assertIsNone(shared_local_identity.application_id)

        local_application = Application.objects.create(
            organization=self.alpha,
            program=self.alpha_program,
            email="local-applicant@example.test",
            status=Application.Status.SUBMITTED,
        )
        local_application_event = self._event(
            self.alpha, "local-applicant@example.test", is_verified=True
        )
        local_application_event.resolve_entities()
        self.assertEqual(local_application_event.application_id, local_application.pk)

        local_only_student = Student.objects.create(
            organization=self.alpha,
            legal_first_name="Casey",
            last_name="Local",
            personal_email="local-only@example.test",
        )
        Student.objects.create(
            organization=self.beta,
            legal_first_name="Casey",
            last_name="Foreign",
            personal_email="local-only@example.test",
        )
        unambiguous_local_identity = self._event(
            self.alpha, "local-only@example.test", is_verified=True
        )
        unambiguous_local_identity.resolve_entities()
        self.assertEqual(unambiguous_local_identity.student_id, local_only_student.pk)
        self.assertIsNone(unambiguous_local_identity.adult_id)
        self.assertIsNone(unambiguous_local_identity.application_id)

        inconsistent_application = Application.objects.create(
            organization=self.alpha,
            program=self.beta_program,
            email="application@example.test",
            status=Application.Status.SUBMITTED,
        )
        mismatched_application_event = self._event(
            self.alpha,
            "application@example.test",
            is_verified=True,
        )
        mismatched_application_event.resolve_entities()
        self.assertIsNone(mismatched_application_event.application_id)
        linked_mismatched_application_event = self._event(
            self.alpha,
            "other-application@example.test",
            application=inconsistent_application,
            is_verified=True,
        )
        self.assertFalse(
            EmailDeliveryEvent.objects.verified_for_organization(self.alpha)
            .filter(pk=linked_mismatched_application_event.pk)
            .exists()
        )

    def test_quarantine_review_is_platform_superuser_only(self):
        quarantine = EmailDeliveryQuarantine.objects.create(
            deduplication_key="a" * 64,
            reason_code=EmailDeliveryQuarantine.REASON_UNMATCHED,
            encrypted_diagnostic=json.dumps(
                {"recipients": ["quarantine@example.test"], "message_id": "id"}
            ),
        )
        self.client.force_login(self.lead_user)

        denied = self.client.get(
            reverse("platform_admin:email_delivery_quarantine"),
            HTTP_HOST="localhost",
        )
        self.assertEqual(denied.status_code, 403)

        superuser = get_user_model().objects.create_superuser(
            username="platform-delivery-superuser",
            email="platform-admin@example.test",
            password="password123",
        )  # nosec B106
        self.client.force_login(superuser)
        allowed = self.client.get(
            reverse("platform_admin:email_delivery_quarantine"),
            HTTP_HOST="localhost",
        )
        self.assertContains(allowed, "quarantine@example.test")
        tenant_host_response = self.client.get(
            reverse("platform_admin:email_delivery_quarantine"),
            HTTP_HOST=self.alpha_host,
        )
        self.assertNotEqual(tenant_host_response.status_code, 200)
        self.assertTrue(
            EmailDeliveryQuarantine.objects.filter(pk=quarantine.pk).exists()
        )
