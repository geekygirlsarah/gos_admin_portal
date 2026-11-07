from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from organizations.models import Organization, UserOrganizationMembership
from programs.models import Enrollment, Program, Student


class StudentActivationTenantPermissionTests(TestCase):
    def setUp(self):
        self.alpha = Organization.objects.create(
            name="Activation Alpha",
            slug="activation-alpha",
            subdomain="activation-alpha",
        )
        self.beta = Organization.objects.create(
            name="Activation Beta",
            slug="activation-beta",
            subdomain="activation-beta",
        )
        self.alpha_program = Program.objects.create(
            organization=self.alpha, name="Alpha Program"
        )
        self.beta_program = Program.objects.create(
            organization=self.beta, name="Beta Program"
        )
        self.alpha_student = Student.objects.create(
            organization=self.alpha, legal_first_name="Alpha", last_name="Student"
        )
        self.beta_student = Student.objects.create(
            organization=self.beta, legal_first_name="Beta", last_name="Student"
        )
        self.alpha_enrollment = Enrollment.objects.create(
            organization=self.alpha,
            program=self.alpha_program,
            student=self.alpha_student,
            active=True,
        )
        self.beta_enrollment = Enrollment.objects.create(
            organization=self.beta,
            program=self.beta_program,
            student=self.beta_student,
            active=True,
        )
        self.lead = get_user_model().objects.create_user(
            username="activation-alpha-lead", password="password123"
        )  # nosec B106
        UserOrganizationMembership.objects.create(
            user=self.lead, organization=self.alpha, role="LeadMentor", is_active=True
        )
        self.client.force_login(self.lead)
        self.alpha_host = "activation-alpha.localhost"

    def tenant_post(self, path):
        return self.client.post(path, HTTP_HOST=self.alpha_host)

    def test_membership_only_lead_can_deactivate_and_reactivate_local_enrollment(self):
        deactivate_url = reverse(
            "program_student_deactivate",
            args=[self.alpha_program.pk, self.alpha_student.pk],
        )
        activate_url = reverse(
            "program_student_activate",
            args=[self.alpha_program.pk, self.alpha_student.pk],
        )

        self.assertEqual(self.tenant_post(deactivate_url).status_code, 302)
        self.alpha_enrollment.refresh_from_db()
        self.assertFalse(self.alpha_enrollment.active)

        self.assertEqual(self.tenant_post(activate_url).status_code, 302)
        self.alpha_enrollment.refresh_from_db()
        self.assertTrue(self.alpha_enrollment.active)

    def test_lead_cannot_deactivate_foreign_enrollment(self):
        url = reverse(
            "program_student_deactivate",
            args=[self.beta_program.pk, self.beta_student.pk],
        )

        response = self.tenant_post(url)

        self.assertNotEqual(response.status_code, 200)
        self.beta_enrollment.refresh_from_db()
        self.assertTrue(self.beta_enrollment.active)
