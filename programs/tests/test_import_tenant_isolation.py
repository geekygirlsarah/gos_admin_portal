from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models.signals import post_init
from django.http import HttpResponse
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.views import View

from attendance.models import AttendanceEvent, AttendanceSession
from GoSAdminPortal.test_runner import (
    _assign_default_test_org,
    install_default_test_org_hook,
)
from organizations.models import Organization, UserOrganizationMembership
from programs.models import (
    Adult,
    AdultStudentRelationship,
    Program,
    ProgramFeature,
    Student,
)
from programs.views.mixins import TenantLeadMentorPermissionRequiredMixin


class _PermissionProtectedView(TenantLeadMentorPermissionRequiredMixin, View):
    permission_required = "programs.add_student"

    def get(self, request):
        return HttpResponse("allowed")


class TenantImportIsolationTests(TestCase):
    tenant_models = (
        Program,
        Student,
        Adult,
        AdultStudentRelationship,
        AttendanceEvent,
        AttendanceSession,
    )

    def setUp(self):
        for model in self.tenant_models:
            post_init.disconnect(
                receiver=_assign_default_test_org,
                sender=model,
                dispatch_uid=f"default-test-org-{model._meta.label}",
            )

        self.alpha = Organization.objects.get(slug="gos")
        self.beta = Organization.objects.create(
            name="Import Beta",
            slug="import-beta",
            subdomain="import-beta",
        )
        self.user = get_user_model().objects.create_user(
            username="import-beta-lead", password="password123"
        )  # nosec B106
        UserOrganizationMembership.objects.create(
            user=self.user,
            organization=self.beta,
            role="LeadMentor",
            is_active=True,
        )
        # The old attendance importer resolves a role from the default tenant.
        # Keep this membership so the regression reaches its unscoped lookups.
        UserOrganizationMembership.objects.create(
            user=self.user,
            organization=self.alpha,
            role="LeadMentor",
            is_active=True,
        )
        permissions = Permission.objects.filter(
            content_type__app_label="programs",
            codename__in=("add_student", "add_adult", "change_student"),
        )
        self.user.user_permissions.add(*permissions)
        self.client.force_login(self.user)

    def tearDown(self):
        install_default_test_org_hook()

    def _post_csv(self, url_name, csv_text, extra_data=None):
        data = {
            "file": SimpleUploadedFile(
                "tenant-import.csv", csv_text.encode("utf-8"), content_type="text/csv"
            )
        }
        if extra_data:
            data.update(extra_data)
        return self.client.post(
            reverse(url_name), data, HTTP_HOST="import-beta.localhost", follow=True
        )

    def test_import_dashboard_lists_only_current_organization_programs(self):
        alpha_program = Program.objects.create(
            organization=self.alpha, name="Alpha Private Program"
        )
        beta_program = Program.objects.create(
            organization=self.beta, name="Beta Local Program"
        )

        response = self.client.get(
            reverse("import_dashboard"), HTTP_HOST="import-beta.localhost"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["programs"]), [beta_program])
        self.assertNotIn(alpha_program, response.context["programs"])

    def test_superuser_import_dashboard_remains_tenant_scoped(self):
        Program.objects.create(organization=self.alpha, name="Alpha Private Program")
        beta_program = Program.objects.create(
            organization=self.beta, name="Beta Local Program"
        )
        admin = get_user_model().objects.create_superuser(
            username="platform-import-admin", email="admin@example.test"
        )
        self.client.force_login(admin)

        response = self.client.get(
            reverse("import_dashboard"), HTTP_HOST="import-beta.localhost"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["programs"]), [beta_program])

    def test_tenant_permission_mixin_does_not_accept_global_permission_in_other_org(
        self,
    ):
        lower_role_user = get_user_model().objects.create_user(
            username="alpha-lead-beta-parent"
        )
        permission = Permission.objects.get(
            content_type__app_label="programs", codename="add_student"
        )
        lower_role_user.user_permissions.add(permission)
        UserOrganizationMembership.objects.create(
            user=lower_role_user,
            organization=self.alpha,
            role="LeadMentor",
            is_active=True,
        )
        UserOrganizationMembership.objects.create(
            user=lower_role_user,
            organization=self.beta,
            role="Parent",
            is_active=True,
        )
        request = RequestFactory().get("/protected/")
        request.user = lower_role_user
        request.organization = self.beta

        with self.assertRaises(PermissionDenied):
            _PermissionProtectedView.as_view()(request)

    def test_tenant_permission_mixin_allows_local_lead_and_denies_missing_org(self):
        permission = Permission.objects.get(
            content_type__app_label="programs", codename="add_student"
        )
        local_lead = get_user_model().objects.create_user(username="local-beta-lead")
        local_lead.user_permissions.add(permission)
        UserOrganizationMembership.objects.create(
            user=local_lead,
            organization=self.beta,
            role="LeadMentor",
            is_active=True,
        )
        request = RequestFactory().get("/protected/")
        request.user = local_lead
        request.organization = self.beta
        self.assertEqual(_PermissionProtectedView.as_view()(request).status_code, 200)

        request = RequestFactory().get("/protected/")
        request.user = local_lead
        request.organization = None
        with self.assertRaises(PermissionDenied):
            _PermissionProtectedView.as_view()(request)

        single_org_member = get_user_model().objects.create_user(
            username="single-org-import-member"
        )
        single_org_member.user_permissions.add(permission)
        UserOrganizationMembership.objects.create(
            user=single_org_member,
            organization=self.beta,
            role="Staff",
            is_active=True,
        )
        request = RequestFactory().get("/protected/")
        request.user = single_org_member
        request.organization = self.beta
        self.assertEqual(_PermissionProtectedView.as_view()(request).status_code, 200)

    def test_student_import_does_not_update_foreign_match_and_sets_new_owner(self):
        foreign_student = Student.objects.create(
            organization=self.alpha,
            legal_first_name="Avery",
            preferred_first_name="Original",
            last_name="Foreign",
            date_of_birth="2010-01-01",
        )
        response = self._post_csv(
            "student_import",
            "First Name,Legal First Name,Last Name,Date of Birth\n"
            "Changed,Avery,Foreign,2010-01-01\n"
            "New,Beta,Student,2011-02-02\n",
            {"overwrite": "1"},
        )

        self.assertEqual(response.status_code, 200)
        foreign_student.refresh_from_db()
        self.assertEqual(foreign_student.preferred_first_name, "Original")
        self.assertTrue(
            Student.objects.filter(
                organization=self.beta,
                legal_first_name="Beta",
                last_name="Student",
            ).exists()
        )

    def test_student_import_creates_local_parent_instead_of_linking_foreign_adult(self):
        foreign_parent = Adult.objects.create(
            organization=self.alpha,
            legal_first_name="Foreign",
            last_name="Parent",
            personal_email="parent@example.test",
            is_parent=True,
        )
        response = self._post_csv(
            "student_import",
            "First Name,Legal First Name,Last Name,Date of Birth,"
            "Primary Parent First Name,Primary Parent Last Name,Primary Parent Email\n"
            "New,Beta,Student,2011-02-02,Local,Parent,parent@example.test\n",
        )

        self.assertEqual(response.status_code, 200)
        student = Student.objects.get(
            organization=self.beta, legal_first_name="Beta", last_name="Student"
        )
        self.assertFalse(student.adults.filter(pk=foreign_parent.pk).exists())
        self.assertEqual(
            list(
                student.adults.filter(organization=self.beta).values_list(
                    "personal_email", flat=True
                )
            ),
            ["parent@example.test"],
        )
        self.assertEqual(student.primary_contact.organization_id, self.beta.pk)
        foreign_parent.refresh_from_db()
        self.assertEqual(foreign_parent.legal_first_name, "Foreign")

    def test_student_contact_setter_rejects_foreign_adult(self):
        student = Student.objects.create(
            organization=self.beta,
            legal_first_name="Local",
            last_name="Student",
        )
        foreign_adult = Adult.objects.create(
            organization=self.alpha,
            legal_first_name="Foreign",
            last_name="Adult",
        )

        with self.assertRaises(ValidationError):
            student.primary_contact = foreign_adult

        self.assertFalse(
            AdultStudentRelationship.objects.filter(
                student=student, adult=foreign_adult
            ).exists()
        )

    def test_parent_import_does_not_overwrite_foreign_adult(self):
        foreign_parent = Adult.objects.create(
            organization=self.alpha,
            legal_first_name="Taylor",
            last_name="Parent",
            personal_email="old@example.test",
            phone_number="111-111-1111",
            is_parent=False,
        )
        response = self._post_csv(
            "parent_import",
            "First Name,Last Name,Email,Phone Number\n"
            "Taylor,Parent,new@example.test,222-222-2222\n",
            {"overwrite": "1"},
        )

        self.assertEqual(response.status_code, 200)
        foreign_parent.refresh_from_db()
        self.assertEqual(foreign_parent.personal_email, "old@example.test")
        self.assertEqual(foreign_parent.phone_number, "111-111-1111")
        self.assertFalse(foreign_parent.is_parent)
        self.assertTrue(
            Adult.objects.filter(
                organization=self.beta,
                legal_first_name="Taylor",
                last_name="Parent",
                personal_email="new@example.test",
            ).exists()
        )

    def test_relationship_import_only_links_local_students_and_sets_relation_owner(
        self,
    ):
        local_student = Student.objects.create(
            organization=self.beta,
            legal_first_name="Local",
            last_name="Student",
            date_of_birth="2010-01-01",
        )
        local_parent = Adult.objects.create(
            organization=self.beta,
            legal_first_name="Local",
            last_name="Parent",
            personal_email="local.parent@example.test",
            is_parent=True,
        )
        foreign_student = Student.objects.create(
            organization=self.alpha,
            legal_first_name="Foreign",
            last_name="Student",
            date_of_birth="2010-01-01",
        )
        csv_text = (
            "student_id,primary_parent_first_name,primary_parent_last_name,"
            "primary_parent_email,primary_parent_relationship\n"
            f"{local_student.pk},Local,Parent,local.parent@example.test,parent\n"
            f"{foreign_student.pk},Local,Parent,local.parent@example.test,parent\n"
        )

        response = self._post_csv("relationship_import", csv_text, {"overwrite": "1"})

        self.assertEqual(response.status_code, 200)
        local_relationship = AdultStudentRelationship.objects.get(
            organization=self.beta, adult=local_parent, student=local_student
        )
        self.assertEqual(local_relationship.relationship_to_student, "parent")
        self.assertFalse(
            AdultStudentRelationship.objects.filter(student=foreign_student).exists()
        )

    def test_mentor_import_does_not_update_foreign_adult(self):
        foreign_mentor = Adult.objects.create(
            organization=self.alpha,
            legal_first_name="Casey",
            last_name="Mentor",
            personal_email="old.mentor@example.test",
            is_mentor=False,
        )
        response = self._post_csv(
            "mentor_import",
            "First Name,Last Name,Personal Email,Role\n"
            "Casey,Mentor,new.mentor@example.test,mentor\n",
            {"overwrite": "1"},
        )

        self.assertEqual(response.status_code, 200)
        foreign_mentor.refresh_from_db()
        self.assertEqual(foreign_mentor.personal_email, "old.mentor@example.test")
        self.assertFalse(foreign_mentor.is_mentor)
        self.assertTrue(
            Adult.objects.filter(
                organization=self.beta,
                legal_first_name="Casey",
                last_name="Mentor",
                personal_email="new.mentor@example.test",
                is_mentor=True,
            ).exists()
        )

    def test_attendance_import_scopes_program_and_student_and_assigns_ownership(self):
        attendance_feature, _ = ProgramFeature.objects.get_or_create(
            key="attendance", defaults={"name": "Attendance"}
        )
        local_program = Program.objects.create(
            organization=self.beta, name="Beta Attendance"
        )
        local_program.features.add(attendance_feature)
        foreign_program = Program.objects.create(
            organization=self.alpha, name="Alpha Attendance"
        )
        foreign_program.features.add(attendance_feature)
        foreign_student = Student.objects.create(
            organization=self.alpha,
            legal_first_name="Foreign",
            preferred_first_name="Foreign",
            last_name="Student",
        )
        csv_text = (
            "first_name,last_name,rfid,time_in_utc\n"
            "Foreign,Student,,2025-09-03T18:05:00Z\n"
        )

        response = self._post_csv(
            "attendance_import", csv_text, {"program_id": str(local_program.pk)}
        )

        self.assertEqual(response.status_code, 200)
        session = AttendanceSession.objects.get(program=local_program)
        self.assertEqual(session.organization_id, self.beta.pk)
        self.assertIsNone(session.student_id)
        self.assertEqual(
            AttendanceEvent.objects.get(program=local_program).organization_id,
            self.beta.pk,
        )
        self.assertTrue(Student.objects.filter(pk=foreign_student.pk).exists())

        self._post_csv(
            "attendance_import", csv_text, {"program_id": str(foreign_program.pk)}
        )
        self.assertFalse(
            AttendanceSession.objects.filter(program=foreign_program).exists()
        )
