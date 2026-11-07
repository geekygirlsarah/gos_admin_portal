from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from badges.forms import BadgeForm
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

    def test_badge_forms_only_offer_same_organization_prerequisites(self):
        response = self.tenant_get(reverse("badges:create"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Alpha Safety")
        self.assertNotContains(response, "Beta Private Badge")

    def test_badge_form_without_organization_has_no_prerequisite_choices(self):
        form = BadgeForm(organization=None)

        self.assertEqual(form.fields["prerequisites"].queryset.count(), 0)

    def test_create_rejects_foreign_prerequisite(self):
        response = self.tenant_post(
            reverse("badges:create"),
            {
                "name": "New Alpha Badge",
                "category": "Basics",
                "level": "1",
                "description": "",
                "skills_required": "",
                "how_to_earn": "",
                "prerequisites": [str(self.beta_badge.pk)],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            Badge.objects.for_organization(self.alpha)
            .filter(name="New Alpha Badge")
            .exists()
        )

    def test_update_rejects_foreign_prerequisite(self):
        response = self.tenant_post(
            reverse("badges:edit", args=[self.alpha_badge.pk]),
            {
                "name": self.alpha_badge.name,
                "category": self.alpha_badge.category,
                "level": str(self.alpha_badge.level),
                "description": self.alpha_badge.description,
                "skills_required": self.alpha_badge.skills_required,
                "how_to_earn": self.alpha_badge.how_to_earn,
                "prerequisites": [str(self.beta_badge.pk)],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            self.alpha_badge.prerequisites.filter(pk=self.beta_badge.pk).exists()
        )

    def test_existing_foreign_prerequisite_is_not_displayed(self):
        local_prerequisite = Badge.objects.create(
            organization=self.alpha, name="Alpha Local Prerequisite"
        )
        self.alpha_badge.prerequisites.add(local_prerequisite, self.beta_badge)

        list_response = self.tenant_get(reverse("badges:list"))
        detail_response = self.tenant_get(
            reverse("badges:detail", args=[self.alpha_badge.pk])
        )

        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, "Alpha Local Prerequisite")
        self.assertNotContains(list_response, "Beta Private Badge")
        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, "Alpha Local Prerequisite")
        self.assertNotContains(detail_response, "Beta Private Badge")
