from io import BytesIO

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from openpyxl import load_workbook

from organizations.models import Organization, UserOrganizationMembership
from programs.models import Enrollment, Program, Student

TEST_FILE_KEY = Fernet.generate_key().decode()


@override_settings(FILE_ENCRYPTION_KEY=TEST_FILE_KEY)
class ProgramSensitiveTenantIsolationTests(TestCase):
    def setUp(self):
        self.alpha = Organization.objects.create(
            name="Sensitive Alpha", slug="sensitive-alpha", subdomain="sensitive-alpha"
        )
        self.beta = Organization.objects.create(
            name="Sensitive Beta", slug="sensitive-beta", subdomain="sensitive-beta"
        )
        self.alpha_program = Program.objects.create(
            organization=self.alpha, name="Alpha Program", active=True
        )
        self.beta_program = Program.objects.create(
            organization=self.beta, name="Beta Program", active=True
        )
        self.user = get_user_model().objects.create_user(
            username="sensitive-alpha-lead", password="password123"
        )  # nosec B106
        UserOrganizationMembership.objects.create(
            user=self.user,
            organization=self.alpha,
            role="LeadMentor",
            is_active=True,
        )
        self.alpha_student = Student.objects.create(
            organization=self.alpha,
            legal_first_name="Alpha",
            last_name="Student",
            personal_email="alpha-student@example.test",
            allergies="Alpha allergy",
        )
        Enrollment.objects.create(
            student=self.alpha_student, program=self.alpha_program, active=True
        )
        self.beta_student = Student.objects.create(
            organization=self.beta,
            legal_first_name="BetaPrivate",
            last_name="Student",
            personal_email="beta-private@example.test",
            allergies="Beta private allergy",
        )
        Enrollment.objects.create(
            student=self.beta_student, program=self.beta_program, active=True
        )
        self.client.force_login(self.user)
        self.alpha_host = "sensitive-alpha.localhost"

    def tenant_get(self, path):
        return self.client.get(path, HTTP_HOST=self.alpha_host)

    def test_membership_only_lead_can_view_own_sensitive_program_pages(self):
        for route in ("program_medical_info", "program_emergency_contacts"):
            with self.subTest(route=route):
                response = self.tenant_get(reverse(route, args=[self.alpha_program.pk]))
                self.assertEqual(response.status_code, 200)

    def test_program_detail_shows_lead_controls_in_current_tenant(self):
        response = self.tenant_get(
            reverse("program_detail", args=[self.alpha_program.pk])
        )

        self.assertEqual(response.status_code, 200)
        for flag in (
            "can_manage_students",
            "can_add_payment",
            "can_manage_fees",
            "can_view_payments",
            "can_view_attendance",
            "can_view_documents",
            "can_manage_documents",
        ):
            with self.subTest(flag=flag):
                self.assertTrue(response.context[flag])

    def test_membership_only_lead_export_uses_current_tenant_role(self):
        response = self.tenant_get(
            f"{reverse('program_student_export', args=[self.alpha_program.pk])}?format=xlsx&groups=contact"
        )

        self.assertEqual(response.status_code, 200)
        worksheet = load_workbook(BytesIO(response.content)).active
        headers = [cell.value for cell in worksheet[1]]
        self.assertIn("Student Email", headers)
        self.assertIn("Student Phone", headers)

    def test_foreign_program_sensitive_pages_and_export_are_not_visible(self):
        paths = (
            reverse("program_medical_info", args=[self.beta_program.pk]),
            reverse("program_emergency_contacts", args=[self.beta_program.pk]),
            reverse("program_student_export", args=[self.beta_program.pk]),
        )
        for path in paths:
            with self.subTest(path=path):
                response = self.tenant_get(path)
                self.assertNotEqual(response.status_code, 200)
                self.assertNotIn(b"BetaPrivate", response.content)
                self.assertNotIn(b"Beta private allergy", response.content)
                self.assertNotIn(b"beta-private@example.test", response.content)
