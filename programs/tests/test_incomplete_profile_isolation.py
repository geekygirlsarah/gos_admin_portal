from io import BytesIO
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from openpyxl import load_workbook

from api.kiosk_views import _active_students_for_kiosk
from applications.models import Application
from organizations.models import Organization, UserOrganizationMembership
from programs.models import (
    Adult,
    AdultStudentRelationship,
    Enrollment,
    Program,
    Student,
)
from programs.signals import sync_adult_memberships, sync_student_memberships
from programs.utils.students import active_students_in_program


class IncompleteProfileIsolationTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(
            name="Profile Isolation",
            slug="profile-isolation",
            subdomain="profile-isolation",
        )
        self.other_organization = Organization.objects.create(
            name="Other Profile Isolation",
            slug="profile-isolation-other",
            subdomain="profile-isolation-other",
        )
        self.program = Program.objects.create(
            organization=self.organization,
            name="Isolation Program",
            active=True,
        )
        self.user = get_user_model().objects.create_superuser(
            username="profile-isolation-admin",
            email="profile-isolation-admin@example.test",
            password=None,
        )
        self.client.force_login(self.user)
        self.host = "profile-isolation.localhost"

        self.student = Student.objects.create(
            organization=self.organization,
            legal_first_name="Visible",
            last_name="Student",
        )
        self.draft_student = Student.all_objects.create(
            organization=self.organization,
            legal_first_name="Hidden",
            last_name="Student",
        )
        self.draft_enrollment = Enrollment.objects.create(
            organization=self.organization,
            student=self.draft_student,
            program=self.program,
        )
        self.draft_student.is_incomplete_application_profile = True
        self.draft_student.save(update_fields=["is_incomplete_application_profile"])
        self.adult = Adult.objects.create(
            organization=self.organization,
            legal_first_name="Visible",
            last_name="Guardian",
            is_parent=True,
        )
        self.draft_adult = Adult.all_objects.create(
            organization=self.organization,
            legal_first_name="Hidden",
            last_name="Guardian",
            is_parent=True,
        )
        self.draft_relationship = AdultStudentRelationship.objects.create(
            organization=self.organization,
            adult=self.draft_adult,
            student=self.student,
        )
        self.draft_adult.is_incomplete_application_profile = True
        self.draft_adult.save(update_fields=["is_incomplete_application_profile"])
        self.enrollment = Enrollment.objects.create(
            organization=self.organization,
            student=self.student,
            program=self.program,
        )

    def tenant_get(self, path):
        return self.client.get(path, HTTP_HOST=self.host)

    def test_default_profile_managers_hide_incomplete_profiles_by_tenant(self):
        self.assertNotIn(
            self.draft_student,
            Student.objects.for_organization(self.organization),
        )
        self.assertIn(
            self.draft_student,
            Student.all_objects.for_organization(self.organization),
        )
        self.assertNotIn(
            self.draft_student,
            Student.all_objects.for_organization(self.other_organization),
        )
        self.assertNotIn(
            self.draft_adult,
            Adult.objects.for_organization(self.organization),
        )
        self.assertIn(
            self.draft_adult,
            Adult.all_objects.for_organization(self.organization),
        )
        self.assertNotIn(
            self.draft_adult,
            Adult.all_objects.for_organization(self.other_organization),
        )

    def test_ordinary_directories_details_and_export_hide_incomplete_profiles(self):
        student_list = self.tenant_get(reverse("student_list"))
        adult_list = self.tenant_get(reverse("adult_list"))

        self.assertEqual(student_list.status_code, 200)
        self.assertContains(student_list, "Visible Student")
        self.assertNotContains(student_list, "Hidden Student")
        self.assertEqual(adult_list.status_code, 200)
        self.assertContains(adult_list, "Visible Guardian")
        self.assertNotContains(adult_list, "Hidden Guardian")
        dashboard = self.tenant_get(reverse("profile_dashboard"))
        self.assertEqual(dashboard.status_code, 200)
        self.assertNotContains(dashboard, "Hidden Student")
        self.assertNotContains(dashboard, "Hidden Guardian")
        student_detail = self.tenant_get(
            reverse("student_detail", args=[self.draft_student.pk])
        )
        adult_detail = self.tenant_get(
            reverse("adult_detail", args=[self.draft_adult.pk])
        )
        self.assertIn(student_detail.status_code, (302, 403, 404))
        self.assertIn(adult_detail.status_code, (302, 403, 404))
        self.assertNotContains(
            student_detail, "Hidden Student", status_code=student_detail.status_code
        )
        self.assertNotContains(
            adult_detail, "Hidden Guardian", status_code=adult_detail.status_code
        )
        other_host_student_detail = self.client.get(
            reverse("student_detail", args=[self.student.pk]),
            HTTP_HOST="profile-isolation-other.localhost",
        )
        other_host_adult_detail = self.client.get(
            reverse("adult_detail", args=[self.adult.pk]),
            HTTP_HOST="profile-isolation-other.localhost",
        )
        self.assertIn(other_host_student_detail.status_code, (302, 403, 404))
        self.assertIn(other_host_adult_detail.status_code, (302, 403, 404))
        self.assertNotContains(
            other_host_student_detail,
            "Visible Student",
            status_code=other_host_student_detail.status_code,
        )
        self.assertNotContains(
            other_host_adult_detail,
            "Visible Guardian",
            status_code=other_host_adult_detail.status_code,
        )

        export = self.tenant_get(
            f"{reverse('program_student_export', args=[self.program.pk])}?format=xlsx"
        )
        self.assertEqual(export.status_code, 200)
        worksheet = load_workbook(BytesIO(export.content)).active
        exported_values = [
            cell.value
            for row in worksheet.iter_rows()
            for cell in row
            if cell.value is not None
        ]
        self.assertIn("Visible", exported_values)
        self.assertNotIn("Hidden", exported_values)

    def test_relationship_and_enrollment_queries_hide_draft_endpoints(self):
        relationship = AdultStudentRelationship.objects.create(
            organization=self.organization,
            adult=self.adult,
            student=self.student,
        )
        self.student.primary_contact_relationship = relationship
        self.student.save(update_fields=["primary_contact_relationship"])
        self.student.is_incomplete_application_profile = True
        self.student.save(update_fields=["is_incomplete_application_profile"])

        self.assertFalse(Enrollment.objects.filter(pk=self.enrollment.pk).exists())
        self.assertFalse(
            Enrollment.objects.filter(pk=self.draft_enrollment.pk).exists()
        )
        self.assertNotIn(self.student, self.program.students.all())
        self.assertNotIn(
            self.student,
            active_students_in_program(self.program, self.organization),
        )
        kiosk_config = SimpleNamespace(
            program=self.program,
            organization=self.organization,
        )
        self.assertNotIn(
            self.student,
            _active_students_for_kiosk(kiosk_config),
        )
        self.assertFalse(
            AdultStudentRelationship.objects.filter(pk=relationship.pk).exists()
        )
        self.assertFalse(
            AdultStudentRelationship.objects.filter(
                pk=self.draft_relationship.pk
            ).exists()
        )
        self.assertEqual(self.draft_adult.all_students(), [])
        self.assertEqual(self.student.primary_contact, None)
        self.assertFalse(self.student.parents.exists())
        self.assertEqual(self.adult.all_students(), [])
        self.student.refresh_from_db()
        self.assertEqual(self.student.all_parents, [])

    def test_incomplete_profiles_cannot_gain_accounts_enrollments_or_relationships(
        self,
    ):
        applicant = get_user_model().objects.create_user(
            username="incomplete-applicant",
            email="incomplete-applicant@example.test",
            password=None,
        )
        self.draft_student.user = applicant
        with self.assertRaises(ValidationError):
            self.draft_student.save()
        sync_student_memberships(self.draft_student)

        self.draft_adult.user = applicant
        with self.assertRaises(ValidationError):
            self.draft_adult.save()
        sync_adult_memberships(self.draft_adult)

        with self.assertRaises(ValidationError):
            Enrollment.objects.create(
                organization=self.organization,
                student=self.draft_student,
                program=self.program,
            )
        with self.assertRaises(ValidationError):
            AdultStudentRelationship.objects.create(
                organization=self.organization,
                adult=self.draft_adult,
                student=self.student,
            )

        self.assertFalse(
            UserOrganizationMembership.objects.filter(user=applicant).exists()
        )

    def test_platform_superuser_can_review_application_linked_to_draft_profile(self):
        application = Application.objects.create(
            organization=self.organization,
            student_profile=self.draft_student,
        )

        response = self.tenant_get(
            reverse("application_review_detail", args=[application.application_id])
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["application"], application)

    def test_lead_mentor_can_review_application_linked_to_draft_profile(self):
        application = Application.objects.create(
            organization=self.organization,
            student_profile=self.draft_student,
        )
        permission, _ = Permission.objects.get_or_create(
            content_type=ContentType.objects.get_for_model(Application),
            codename="review_application",
            defaults={"name": "Can review applications"},
        )
        group, _ = Group.objects.get_or_create(name="LeadMentor")
        group.permissions.add(permission)
        reviewer = get_user_model().objects.create_user(
            username="draft-profile-reviewer",
            email="draft-reviewer@example.test",
            password=None,
        )
        reviewer.groups.add(group)
        UserOrganizationMembership.objects.create(
            user=reviewer,
            organization=self.organization,
            role="LeadMentor",
            is_active=True,
        )
        self.client.force_login(reviewer)

        response = self.tenant_get(
            reverse("application_review_detail", args=[application.application_id])
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["application"], application)
