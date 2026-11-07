from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from organizations.models import Organization, UserOrganizationMembership
from programs.models import Adult, Student


class OrganizationAccountTenantIsolationTests(TestCase):
    def setUp(self):
        self.alpha = Organization.objects.create(
            name="Account Alpha", slug="account-alpha", subdomain="account-alpha"
        )
        self.beta = Organization.objects.create(
            name="Account Beta", slug="account-beta", subdomain="account-beta"
        )
        self.lead = get_user_model().objects.create_user(
            username="account-alpha-lead", password="password123"
        )  # nosec B106
        UserOrganizationMembership.objects.create(
            user=self.lead, organization=self.alpha, role="LeadMentor", is_active=True
        )
        self.alpha_student = Student.objects.create(
            organization=self.alpha,
            legal_first_name="Alpha",
            last_name="Student",
        )
        self.beta_student = Student.objects.create(
            organization=self.beta,
            legal_first_name="BetaPrivate",
            last_name="Student",
            org_account_id="betaprivate",
            org_email="betaprivate@example.test",
        )
        self.beta_mentor = Adult.objects.create(
            organization=self.beta,
            legal_first_name="Beta",
            last_name="Mentor",
            is_mentor=True,
            org_account_id="betamentor",
        )
        self.client.force_login(self.lead)
        self.alpha_host = "account-alpha.localhost"
        self.url = reverse("org_email_management")

    def tenant_get(self, path=None, data=None):
        return self.client.get(
            path or self.url, data=data or {}, HTTP_HOST=self.alpha_host
        )

    def tenant_post(self, data):
        return self.client.post(self.url, data, HTTP_HOST=self.alpha_host)

    def test_search_and_assigned_list_exclude_foreign_account_data(self):
        assigned = self.tenant_get()
        search = self.tenant_get(data={"q": "BetaPrivate"})

        self.assertEqual(assigned.status_code, 200)
        self.assertNotContains(assigned, "betaprivate")
        self.assertNotContains(assigned, "betaprivate@example.test")
        self.assertEqual(search.status_code, 200)
        self.assertNotContains(search, "Beta Mentor")
        self.assertNotContains(search, "betaprivate@example.test")

    def test_set_and_clear_actions_cannot_change_foreign_account(self):
        self.tenant_post(
            {
                "action": "set",
                "person_type": "student",
                "person_id": self.beta_student.pk,
                "org_account_id": "newforeignid",
            }
        )
        self.beta_student.refresh_from_db()
        self.assertEqual(self.beta_student.org_account_id, "betaprivate")

        self.tenant_post(
            {
                "action": "clear",
                "person_type": "student",
                "person_id": self.beta_student.pk,
            }
        )
        self.beta_student.refresh_from_db()
        self.assertEqual(self.beta_student.org_account_id, "betaprivate")
        self.assertEqual(self.beta_student.org_email, "betaprivate@example.test")

    def test_foreign_adult_cannot_be_selected_as_account_sponsor(self):
        response = self.tenant_post(
            {
                "action": "set",
                "person_type": "student",
                "person_id": self.alpha_student.pk,
                "org_account_id": "alphastudent",
                "org_email_sponsor": self.beta_mentor.pk,
            }
        )

        self.assertNotEqual(response.status_code, 200)
        self.alpha_student.refresh_from_db()
        self.assertIsNone(self.alpha_student.org_account_id)
