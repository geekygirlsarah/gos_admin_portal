from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from organizations.models import Organization, UserOrganizationMembership
from programs.models import Enrollment, Program, ProgramDocument, Student


class ProgramAdminLeadPermissionTests(TestCase):
    def setUp(self):
        self.alpha = Organization.objects.create(
            name="Admin Alpha", slug="admin-alpha", subdomain="admin-alpha"
        )
        self.beta = Organization.objects.create(
            name="Admin Beta", slug="admin-beta", subdomain="admin-beta"
        )
        self.alpha_program = Program.objects.create(
            organization=self.alpha, name="Alpha Program"
        )
        self.beta_program = Program.objects.create(
            organization=self.beta, name="Beta Program"
        )
        self.beta_document = ProgramDocument.objects.create(
            program=self.beta_program,
            name="Beta Private Document",
            file="blank-beta-document.pdf",
        )
        self.lead = get_user_model().objects.create_user(
            username="admin-alpha-lead", password="password123"
        )  # nosec B106
        UserOrganizationMembership.objects.create(
            user=self.lead, organization=self.alpha, role="LeadMentor", is_active=True
        )
        self.client.force_login(self.lead)
        self.alpha_host = "admin-alpha.localhost"

    def tenant_get(self, path):
        return self.client.get(path, HTTP_HOST=self.alpha_host)

    def tenant_post(self, path, data=None):
        return self.client.post(path, data or {}, HTTP_HOST=self.alpha_host)

    def test_membership_only_lead_can_open_program_create_and_edit_forms(self):
        create_response = self.tenant_get(reverse("program_create"))
        edit_response = self.tenant_get(
            reverse("program_edit", args=[self.alpha_program.pk])
        )

        self.assertEqual(create_response.status_code, 200)
        self.assertEqual(edit_response.status_code, 200)

    def test_membership_only_lead_can_open_local_document_create_form(self):
        response = self.tenant_get(
            reverse("program_document_create", args=[self.alpha_program.pk])
        )

        self.assertEqual(response.status_code, 200)

    def test_membership_only_lead_can_open_individual_create_forms(self):
        for route in (
            "student_create",
            "adult_create",
            "mentor_create",
            "parent_create",
        ):
            with self.subTest(route=route):
                response = self.tenant_get(reverse(route))
                self.assertEqual(response.status_code, 200)

    def test_membership_only_lead_can_open_local_school_management_forms(self):
        for route in ("school_create", "school_district_create"):
            with self.subTest(route=route):
                response = self.tenant_get(reverse(route))
                self.assertEqual(response.status_code, 200)

        schools = self.tenant_get(reverse("school_list"))
        districts = self.tenant_get(reverse("school_district_list"))
        self.assertContains(schools, "Add School")
        self.assertContains(schools, "Merge Schools")
        self.assertContains(districts, "Add District")

    def test_program_student_add_rejects_foreign_student_id(self):
        foreign_student = Student.objects.create(
            organization=self.beta,
            legal_first_name="Foreign",
            last_name="Student",
        )

        response = self.tenant_post(
            reverse("program_student_add", args=[self.alpha_program.pk]),
            {"student": foreign_student.pk},
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            Enrollment.objects.filter(
                student=foreign_student, program=self.alpha_program
            ).exists()
        )

    def test_quick_created_student_and_enrollment_belong_to_current_tenant(self):
        response = self.tenant_post(
            reverse("program_student_quick_create", args=[self.alpha_program.pk]),
            {"preferred_first_name": "Quick", "last_name": "Student"},
        )

        self.assertEqual(response.status_code, 302)
        student = Student.objects.get(preferred_first_name="Quick")
        self.assertEqual(student.organization, self.alpha)
        self.assertTrue(
            Enrollment.objects.filter(
                student=student, program=self.alpha_program, organization=self.alpha
            ).exists()
        )

    def test_foreign_program_and_document_management_are_not_visible(self):
        paths = (
            reverse("program_edit", args=[self.beta_program.pk]),
            reverse("program_document_create", args=[self.beta_program.pk]),
            reverse(
                "program_document_edit",
                args=[self.beta_program.pk, self.beta_document.pk],
            ),
            reverse(
                "program_document_delete",
                args=[self.beta_program.pk, self.beta_document.pk],
            ),
        )
        for path in paths:
            with self.subTest(path=path):
                response = self.tenant_get(path)
                self.assertNotEqual(response.status_code, 200)
                self.assertNotIn(b"Beta Private Document", response.content)
