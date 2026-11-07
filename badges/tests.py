from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from badges.models import Badge
from organizations.models import Organization, UserOrganizationMembership
from organizations.utils import get_default_organization


class BadgeTenantIsolationTests(TestCase):
    def setUp(self):
        self.alpha = Organization.objects.create(
            name="Badge Alpha", slug="badge-alpha", subdomain="badge-alpha"
        )
        self.beta = Organization.objects.create(
            name="Badge Beta", slug="badge-beta", subdomain="badge-beta"
        )
        self.alpha_badge = Badge.objects.create(
            organization=self.alpha, name="Alpha Safety"
        )
        self.beta_badge = Badge.objects.create(
            organization=self.beta, name="Beta Private Badge"
        )
        self.lead = get_user_model().objects.create_user(
            username="badge-alpha-lead", password="password123"
        )  # nosec B106
        UserOrganizationMembership.objects.create(
            user=self.lead, organization=self.alpha, role="LeadMentor", is_active=True
        )
        self.alpha_host = "badge-alpha.localhost"
        self.client.force_login(self.lead)

    def tenant_get(self, path):
        return self.client.get(path, HTTP_HOST=self.alpha_host)

    def tenant_post(self, path, data=None):
        return self.client.post(path, data=data or {}, HTTP_HOST=self.alpha_host)

    def test_membership_only_lead_sees_local_badge_controls(self):
        response = self.tenant_get(reverse("badges:list"))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["can_manage"])
        self.assertTrue(response.context["can_award"])
        self.assertTrue(response.context["is_lead_mentor"])

    def test_foreign_badge_detail_is_not_visible(self):
        response = self.tenant_get(reverse("badges:detail", args=[self.beta_badge.pk]))

        self.assertNotEqual(response.status_code, 200)
        self.assertNotIn(b"Beta Private Badge", response.content)

    def test_lead_with_multiple_memberships_cannot_delete_foreign_badge(self):
        UserOrganizationMembership.objects.create(
            user=self.lead,
            organization=get_default_organization(),
            role="LeadMentor",
            is_active=True,
        )

        response = self.tenant_post(reverse("badges:delete", args=[self.beta_badge.pk]))

        self.assertNotEqual(response.status_code, 200)
        self.assertTrue(Badge.objects.filter(pk=self.beta_badge.pk).exists())
