from django.contrib.auth import get_user_model
from django.db.models.signals import post_init
from django.http import Http404
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from audit.events import AuditEvent
from audit.models import AuditLog
from GoSAdminPortal.test_runner import _assign_default_test_org
from organizations.models import (
    Organization,
    OrganizationSettings,
    UserOrganizationMembership,
)
from programs.models import Adult, AdultStudentRelationship, Student
from programs.views.portal_settings import PortalPaymentInstructionsView


@override_settings(
    ALLOWED_HOSTS=[".localhost", "testserver"],
    MOMENTUM_ROOT_DOMAIN="localhost",
)
class PaymentInstructionsTenantTests(TestCase):
    tenant_models = (Adult, AdultStudentRelationship, Student)

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

        User = get_user_model()
        self.alpha = Organization.objects.create(
            name="Payment Alpha", slug="payment-alpha", subdomain="payment-alpha"
        )
        self.beta = Organization.objects.create(
            name="Payment Beta", slug="payment-beta", subdomain="payment-beta"
        )
        self.alpha_host = "payment-alpha.localhost"
        self.beta_host = "payment-beta.localhost"

        self.alpha_lead = User.objects.create_user(
            username="payment-alpha-lead", password="password123"  # nosec B106
        )
        UserOrganizationMembership.objects.create(
            user=self.alpha_lead,
            organization=self.alpha,
            role="LeadMentor",
            is_active=True,
        )
        UserOrganizationMembership.objects.create(
            user=self.alpha_lead,
            organization=self.beta,
            role="Mentor",
            is_active=True,
        )
        self.alpha_parent = self._create_parent("alpha", self.alpha)
        self.beta_parent = self._create_parent("beta", self.beta)

        self.alpha_settings = OrganizationSettings.objects.create(
            organization=self.alpha,
            data={
                "unrelated": {"keep": True},
                "email_settings": {"sender_display_name": "Alpha"},
                "payment_settings": {
                    "instructions": "Alpha payment instructions",
                    "online_payment_url": "https://pay.alpha.example.test/",
                },
            },
        )
        self.beta_settings = OrganizationSettings.objects.create(
            organization=self.beta,
            data={
                "payment_settings": {
                    "instructions": "Beta payment instructions",
                    "online_payment_url": "https://pay.beta.example.test/",
                }
            },
        )

    def _create_parent(self, suffix, organization):
        User = get_user_model()
        user = User.objects.create_user(
            username=f"payment-{suffix}-parent", password="password123"  # nosec B106
        )
        parent = Adult.objects.create(
            user=user,
            organization=organization,
            legal_first_name="Pat",
            last_name=suffix.title(),
            is_parent=True,
        )
        student = Student.objects.create(
            organization=organization,
            legal_first_name="Student",
            last_name=suffix.title(),
        )
        AdultStudentRelationship.objects.create(
            organization=organization,
            adult=parent,
            student=student,
            relationship_to_student="parent",
        )
        UserOrganizationMembership.objects.update_or_create(
            user=user,
            organization=organization,
            role="Parent",
            defaults={"is_active": True},
        )
        return user

    def _restore_default_organization_hook(self):
        for model in self.disconnected_models:
            post_init.connect(
                receiver=_assign_default_test_org,
                sender=model,
                dispatch_uid=f"default-test-org-{model._meta.label}",
            )

    def test_parent_payments_render_only_the_resolved_organizations_settings(self):
        self.client.force_login(self.alpha_parent)
        alpha_response = self.client.get(
            reverse("parent_payments"), HTTP_HOST=self.alpha_host
        )
        self.assertContains(alpha_response, "Alpha payment instructions")
        self.assertContains(alpha_response, "https://pay.alpha.example.test/")
        self.assertNotContains(alpha_response, "Beta payment instructions")
        self.assertNotContains(alpha_response, "Lynnetta Miller")

        self.client.force_login(self.beta_parent)
        beta_response = self.client.get(
            reverse("parent_payments"), HTTP_HOST=self.beta_host
        )
        self.assertContains(beta_response, "Beta payment instructions")
        self.assertContains(beta_response, "https://pay.beta.example.test/")
        self.assertNotContains(beta_response, "Alpha payment instructions")

    @override_settings(PARENT_PAYMENTS_ONLINE_PORTAL_URL="https://global.example.test/")
    def test_blank_instructions_use_generic_fallback_without_global_payment_url(self):
        self.alpha_settings.data["payment_settings"] = {
            "instructions": " \n  ",
            "online_payment_url": "https://pay.alpha.example.test/",
        }
        self.alpha_settings.save(update_fields=["data", "updated_at"])
        self.client.force_login(self.alpha_parent)

        response = self.client.get(
            reverse("parent_payments"), HTTP_HOST=self.alpha_host
        )

        self.assertContains(
            response,
            "Please contact the lead mentors to learn more about how to pay these dues.",
        )
        self.assertNotContains(response, "global.example.test")
        self.assertContains(response, "https://pay.alpha.example.test/")
        self.assertContains(response, "Go to Online Payment Portal")

    def test_payment_text_is_escaped_and_invalid_saved_url_is_hidden(self):
        self.alpha_settings.data["payment_settings"] = {
            "instructions": "<script>alert('unsafe')</script>",
            "online_payment_url": "javascript:alert(1)",
        }
        self.alpha_settings.save(update_fields=["data", "updated_at"])
        self.client.force_login(self.alpha_parent)

        response = self.client.get(
            reverse("parent_payments"), HTTP_HOST=self.alpha_host
        )

        self.assertContains(response, "&lt;script&gt;")
        self.assertNotContains(response, "<script>alert('unsafe')</script>")
        self.assertNotContains(response, "javascript:alert")
        self.assertNotContains(response, "Go to Online Payment Portal")

    def test_payment_settings_tab_renders_the_tenant_specific_form(self):
        self.client.force_login(self.alpha_lead)

        response = self.client.get(
            reverse("portal_settings"),
            {"tab": "payment_instructions"},
            HTTP_HOST=self.alpha_host,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Payment Instructions")
        self.assertContains(response, "Alpha payment instructions")
        self.assertContains(response, "https://pay.alpha.example.test/")

    def test_lead_mentor_updates_only_local_payment_settings(self):
        self.client.force_login(self.alpha_lead)

        response = self.client.post(
            reverse("portal_payment_instructions"),
            {
                "payment_instructions": "Please include the student name.\nMail dues to Alpha.",
                "online_payment_url": "https://new-pay.alpha.example.test/",
            },
            HTTP_HOST=self.alpha_host,
        )

        self.assertEqual(response.status_code, 302)
        self.alpha_settings.refresh_from_db()
        self.beta_settings.refresh_from_db()
        self.assertEqual(self.alpha_settings.data["unrelated"], {"keep": True})
        self.assertEqual(
            self.alpha_settings.data["email_settings"]["sender_display_name"],
            "Alpha",
        )
        self.assertEqual(
            self.alpha_settings.data["payment_settings"],
            {
                "instructions": "Please include the student name.\nMail dues to Alpha.",
                "online_payment_url": "https://new-pay.alpha.example.test/",
            },
        )
        self.assertEqual(
            self.beta_settings.data["payment_settings"]["instructions"],
            "Beta payment instructions",
        )
        self.assertTrue(
            AuditLog.objects.filter(
                event=AuditEvent.ORGANIZATION_UPDATED,
                resource_id=str(self.alpha.pk),
                notes="Updated parent payment instructions.",
            ).exists()
        )

    def test_lead_mentor_can_clear_payment_instructions_and_online_url(self):
        self.client.force_login(self.alpha_lead)

        response = self.client.post(
            reverse("portal_payment_instructions"),
            {"payment_instructions": "  \n ", "online_payment_url": ""},
            HTTP_HOST=self.alpha_host,
        )

        self.assertEqual(response.status_code, 302)
        self.alpha_settings.refresh_from_db()
        self.assertEqual(
            self.alpha_settings.data["payment_settings"]["instructions"], ""
        )
        self.assertEqual(
            self.alpha_settings.data["payment_settings"]["online_payment_url"], ""
        )

    def test_invalid_online_url_does_not_change_payment_settings(self):
        self.client.force_login(self.alpha_lead)

        for unsafe_url in (
            "javascript:alert(1)",
            "ftp://payments.example.test/",
            "/relative/path",
        ):
            with self.subTest(url=unsafe_url):
                response = self.client.post(
                    reverse("portal_payment_instructions"),
                    {
                        "payment_instructions": "Changed instructions",
                        "online_payment_url": unsafe_url,
                    },
                    HTTP_HOST=self.alpha_host,
                )
                self.assertEqual(response.status_code, 302)
                self.alpha_settings.refresh_from_db()
                self.assertEqual(
                    self.alpha_settings.data["payment_settings"]["instructions"],
                    "Alpha payment instructions",
                )

    def test_lower_role_in_another_tenant_cannot_change_payment_settings(self):
        self.client.force_login(self.alpha_lead)

        response = self.client.post(
            reverse("portal_payment_instructions"),
            {
                "payment_instructions": "Cross-tenant change",
                "online_payment_url": "https://attacker.example.test/",
            },
            HTTP_HOST=self.beta_host,
        )

        self.assertEqual(response.status_code, 302)
        self.alpha_settings.refresh_from_db()
        self.beta_settings.refresh_from_db()
        self.assertEqual(
            self.alpha_settings.data["payment_settings"]["instructions"],
            "Alpha payment instructions",
        )
        self.assertEqual(
            self.beta_settings.data["payment_settings"]["instructions"],
            "Beta payment instructions",
        )

    def test_payment_settings_reject_missing_organization_context(self):
        User = get_user_model()
        superuser = User.objects.create_superuser(
            username="payment-platform-admin",
            email="platform-admin@example.test",
            password="password123",  # nosec B106
        )
        request = RequestFactory().post(
            reverse("portal_payment_instructions"),
            {
                "payment_instructions": "Must not be saved",
                "online_payment_url": "https://pay.example.test/",
            },
        )
        request.user = superuser
        request.organization = None
        request.session = self.client.session

        with self.assertRaises(Http404):
            PortalPaymentInstructionsView.as_view()(request)
