"""Comprehensive permission tests: object-level access, program-scoped
permissions, finance permissions, mentor adult access, role protection,
portal permissions updates, and get_user_role."""

from datetime import timedelta

from django.contrib.auth.models import Group, Permission, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from programs.models import (
    Adult,
    AdultStudentRelationship,
    Enrollment,
    Program,
    RolePermission,
    Student,
)
from programs.permission_views import can_user_read


class ProfilePermissionsTests(TestCase):
    def setUp(self):
        self.student_user = User.objects.create_user(
            username="student_user", password="password123", email="student@example.com"
        )  # nosec B106
        self.student = Student.objects.create(
            user=self.student_user,
            first_name="Student",
            last_name="One",
            personal_email="student@example.com",
        )
        self.other_student_user = User.objects.create_user(
            username="other_student", password="password123"
        )  # nosec B106
        self.other_student = Student.objects.create(
            user=self.other_student_user, first_name="Other", last_name="Student"
        )
        self.parent_user = User.objects.create_user(
            username="parent_user", password="password123"
        )  # nosec B106
        self.parent = Adult.objects.create(
            user=self.parent_user, first_name="Parent", last_name="One", is_parent=True
        )
        AdultStudentRelationship.objects.create(
            adult=self.parent, student=self.student, relationship_to_student="parent"
        )
        self.other_parent_user = User.objects.create_user(
            username="other_parent", password="password123"
        )  # nosec B106
        self.other_parent = Adult.objects.create(
            user=self.other_parent_user,
            first_name="Other",
            last_name="Parent",
            is_parent=True,
        )
        self.mentor_user = User.objects.create_user(
            username="mentor_user", password="password123"
        )  # nosec B106
        self.mentor = Adult.objects.create(
            user=self.mentor_user, first_name="Mentor", last_name="One", is_mentor=True
        )
        self.alumni_user = User.objects.create_user(
            username="alumni_user", password="password123"
        )  # nosec B106
        self.alumni = Adult.objects.create(
            user=self.alumni_user,
            first_name="Alumni",
            last_name="One",
            is_alumni=True,
            student_record=self.other_student,
        )
        self.lead_mentor_user = User.objects.create_superuser(
            username="lead_mentor", password="password123"
        )  # nosec B106
        RolePermission.objects.update_or_create(
            role="Student",
            section="student_info",
            defaults={"can_read": True, "can_write": True},
        )
        RolePermission.objects.update_or_create(
            role="Parent",
            section="student_info",
            defaults={"can_read": True, "can_write": True},
        )
        RolePermission.objects.update_or_create(
            role="Parent",
            section="adult_info",
            defaults={"can_read": True, "can_write": True},
        )
        RolePermission.objects.update_or_create(
            role="Mentor",
            section="adult_info",
            defaults={"can_read": True, "can_write": True},
        )
        RolePermission.objects.update_or_create(
            role="Alumni",
            section="adult_info",
            defaults={"can_read": True, "can_write": True},
        )
        RolePermission.objects.update_or_create(
            role="Alumni",
            section="student_info",
            defaults={"can_read": True, "can_write": True},
        )
        self.change_student_perm = Permission.objects.get(codename="change_student")
        self.change_adult_perm = Permission.objects.get(codename="change_adult")

    def test_student_can_view_own_profile(self):
        self.client.login(username="student_user", password="password123")  # nosec B106
        url = reverse("student_detail", args=[self.student.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Student One")

    def test_student_cannot_view_other_student_profile(self):
        self.client.login(username="student_user", password="password123")  # nosec B106
        url = reverse("student_detail", args=[self.other_student.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

    def test_student_can_edit_own_profile(self):
        self.student_user.user_permissions.add(self.change_student_perm)
        self.client.login(username="student_user", password="password123")  # nosec B106
        url = reverse("student_edit", args=[self.student.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_student_cannot_edit_other_student_profile(self):
        self.student_user.user_permissions.add(self.change_student_perm)
        self.client.login(username="student_user", password="password123")  # nosec B106
        url = reverse("student_edit", args=[self.other_student.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

    def test_parent_can_view_own_profile(self):
        self.client.login(username="parent_user", password="password123")  # nosec B106
        url = reverse("adult_detail", args=[self.parent.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_parent_cannot_view_other_adult_profile(self):
        self.client.login(username="parent_user", password="password123")  # nosec B106
        url = reverse("adult_detail", args=[self.other_parent.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

    def test_parent_can_edit_own_profile(self):
        self.parent_user.user_permissions.add(self.change_adult_perm)
        self.client.login(username="parent_user", password="password123")  # nosec B106
        url = reverse("parent_edit", args=[self.parent.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_parent_cannot_edit_other_adult_profile(self):
        self.parent_user.user_permissions.add(self.change_adult_perm)
        self.client.login(username="parent_user", password="password123")  # nosec B106
        url = reverse("parent_edit", args=[self.other_parent.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

    def test_parent_can_view_linked_student(self):
        self.client.login(username="parent_user", password="password123")  # nosec B106
        url = reverse("student_detail", args=[self.student.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_parent_cannot_view_unlinked_student(self):
        self.client.login(username="parent_user", password="password123")  # nosec B106
        url = reverse("student_detail", args=[self.other_student.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

    def test_mentor_can_view_own_profile(self):
        self.client.login(username="mentor_user", password="password123")  # nosec B106
        url = reverse("adult_detail", args=[self.mentor.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_mentor_cannot_view_other_adult_profile(self):
        self.client.login(username="mentor_user", password="password123")  # nosec B106
        url = reverse("adult_detail", args=[self.other_parent.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

    def test_alumni_can_view_own_profile(self):
        self.client.login(username="alumni_user", password="password123")  # nosec B106
        url = reverse("adult_detail", args=[self.alumni.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_alumni_cannot_view_other_adult_profile(self):
        self.client.login(username="alumni_user", password="password123")  # nosec B106
        url = reverse("adult_detail", args=[self.parent.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

    def test_alumni_can_view_own_student_record(self):
        self.client.login(username="alumni_user", password="password123")  # nosec B106
        url = reverse("student_detail", args=[self.other_student.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_lead_mentor_can_view_any_profile(self):
        self.client.login(username="lead_mentor", password="password123")  # nosec B106
        url = reverse("student_detail", args=[self.student.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        url = reverse("adult_detail", args=[self.parent.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_student_list_is_filtered_for_parent(self):
        self.client.login(username="parent_user", password="password123")  # nosec B106
        url = reverse("student_list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Student One")
        self.assertNotContains(response, "Other Student")

    def test_student_list_is_filtered_for_student(self):
        self.client.login(username="student_user", password="password123")  # nosec B106
        url = reverse("student_list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Student One")
        self.assertNotContains(response, "Other Student")

    def test_emergency_contacts_is_filtered_for_parent(self):
        self.client.login(username="parent_user", password="password123")  # nosec B106
        url = reverse("student_emergency_contacts")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Student One")
        self.assertNotContains(response, "Other Student")


class ParentStudentEditTests(TestCase):
    def setUp(self):
        self.parent_user = User.objects.create_user(
            username="parent_user", password="password123"
        )  # nosec B106
        self.parent_adult = Adult.objects.create(
            user=self.parent_user, first_name="Parent", last_name="One", is_parent=True
        )
        self.child = Student.objects.create(first_name="Child", last_name="One")
        AdultStudentRelationship.objects.create(
            adult=self.parent_adult,
            student=self.child,
            relationship_to_student="parent",
        )
        self.other_student = Student.objects.create(
            first_name="Other", last_name="Student"
        )
        RolePermission.objects.update_or_create(
            role="Parent",
            section="student_info",
            defaults={"can_read": True, "can_write": False},
        )

    def test_parent_can_view_child_detail(self):
        self.client.login(username="parent_user", password="password123")  # nosec B106
        url = reverse("student_detail", args=[self.child.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_parent_cannot_view_other_student_detail(self):
        self.client.login(username="parent_user", password="password123")  # nosec B106
        url = reverse("student_detail", args=[self.other_student.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("home"))

    def test_parent_can_edit_child_student_even_if_can_write_is_false(self):
        self.client.login(username="parent_user", password="password123")  # nosec B106
        url = reverse("student_edit", args=[self.child.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_parent_can_edit_child_student_if_can_write_is_true(self):
        RolePermission.objects.filter(role="Parent", section="student_info").update(
            can_write=True
        )
        self.client.login(username="parent_user", password="password123")  # nosec B106
        url = reverse("student_edit", args=[self.child.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_parent_cannot_edit_other_student_even_if_can_write_is_true(self):
        RolePermission.objects.filter(role="Parent", section="student_info").update(
            can_write=True
        )
        self.client.login(username="parent_user", password="password123")  # nosec B106
        url = reverse("student_edit", args=[self.other_student.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("home"))


class StudentEditUpdatesExistingRecordTest(TestCase):
    """Editing a Student via StudentUpdateView must update the existing record."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="staff", password="pass12345"  # nosec B106
        )
        perm = Permission.objects.get(codename="change_student")
        self.user.user_permissions.add(perm)
        group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.user.groups.add(group)
        self.client.login(username="staff", password="pass12345")  # nosec B106
        self.student = Student.objects.create(
            legal_first_name="Jane",
            last_name="SMITH",
            date_of_birth=timezone.datetime(2008, 5, 15).date(),
        )

    def test_edit_updates_existing_student_not_creates_new(self):
        url = reverse("student_edit", args=[self.student.pk])
        data = {
            "legal_first_name": "Jane",
            "last_name": "Smith",
            "date_of_birth": "2008-05-15",
        }
        resp = self.client.post(url, data)
        self.assertEqual(resp.status_code, 302)
        self.assertRedirects(resp, reverse("student_detail", args=[self.student.pk]))
        self.assertEqual(Student.objects.count(), 1)
        self.student.refresh_from_db()
        self.assertEqual(self.student.last_name, "Smith")

    def test_edit_all_caps_last_name_updates_in_place(self):
        original_pk = self.student.pk
        url = reverse("student_edit", args=[self.student.pk])
        data = {
            "legal_first_name": "Jane",
            "last_name": "SMITH",
            "date_of_birth": "2008-05-15",
        }
        resp = self.client.post(url, data)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Student.objects.count(), 1)
        self.assertEqual(Student.objects.first().pk, original_pk)


class ApplicationConversionDeduplicatesStudentTest(TestCase):
    """Application conversion must not create a duplicate Student."""

    def setUp(self):
        self.program = Program.objects.create(name="Robotics 2026")
        self.existing_student = Student.objects.create(
            legal_first_name="Jane",
            last_name="SMITH",
            date_of_birth=timezone.datetime(2008, 5, 15).date(),
        )

    def _make_application(self, last_name):
        from applications.models import Application

        app = Application.objects.create(
            email="jane.smith.applicant@example.com",
            program=self.program,
            applicant_type=Application.Type.STUDENT,
            status=Application.Status.APPROVED_SIGNED,
            data={
                "step5-student": {
                    "legal_first_name": "Jane",
                    "last_name": last_name,
                    "date_of_birth": "2008-05-15",
                    "personal_email": "different@example.com",
                },
            },
        )
        return app

    def test_conversion_matches_existing_student_case_insensitive(self):
        from applications.services import convert_application_to_student

        app = self._make_application("Smith")
        result = convert_application_to_student(app)
        self.assertEqual(Student.objects.count(), 1)
        self.assertEqual(result.pk, self.existing_student.pk)

    def test_conversion_does_not_create_duplicate_for_all_caps_name(self):
        from applications.services import convert_application_to_student

        app = self._make_application("SMITH")
        result = convert_application_to_student(app)
        self.assertEqual(Student.objects.count(), 1)
        self.assertEqual(result.pk, self.existing_student.pk)


class ProgramPermissionTests(TestCase):
    def setUp(self):
        self.lead_mentor_user = User.objects.create_user(
            username="lead_mentor", password="password123"
        )  # nosec B106
        lm_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.lead_mentor_user.groups.add(lm_group)
        self.mentor_user = User.objects.create_user(
            username="mentor_user", password="password123"
        )  # nosec B106
        self.mentor_adult = Adult.objects.create(
            user=self.mentor_user, first_name="Mentor", last_name="User", is_mentor=True
        )
        self.parent_user = User.objects.create_user(
            username="parent_user", password="password123"
        )  # nosec B106
        self.parent_adult = Adult.objects.create(
            user=self.parent_user, first_name="Parent", last_name="User", is_parent=True
        )
        self.student_user = User.objects.create_user(
            username="student_user", password="password123"
        )  # nosec B106
        self.student_profile = Student.objects.create(
            user=self.student_user, first_name="Student", last_name="User"
        )
        today = timezone.now().date()
        self.active_program = Program.objects.create(
            name="Active Program",
            active=True,
            start_date=today - timedelta(days=10),
            end_date=today + timedelta(days=10),
        )
        self.past_program = Program.objects.create(
            name="Past Program",
            active=True,
            start_date=today - timedelta(days=30),
            end_date=today - timedelta(days=10),
        )
        self.upcoming_program = Program.objects.create(
            name="Upcoming Program",
            active=True,
            start_date=today + timedelta(days=10),
            end_date=today + timedelta(days=30),
        )
        self.inactive_program = Program.objects.create(
            name="Inactive Program",
            active=False,
            start_date=today - timedelta(days=10),
            end_date=today + timedelta(days=10),
        )
        sections = RolePermission.SECTION_CHOICES
        roles = ["Mentor", "Parent", "Student"]
        for role in roles:
            for section_code, section_name in sections:
                RolePermission.objects.get_or_create(role=role, section=section_code)

    def test_student_cannot_view_program_detail(self):
        self.client.login(username="student_user", password="password123")  # nosec B106
        url = reverse("program_detail", args=[self.active_program.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("home"))

    def test_parent_cannot_view_program_detail(self):
        self.client.login(username="parent_user", password="password123")  # nosec B106
        url = reverse("program_detail", args=[self.active_program.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("home"))

    def test_mentor_can_view_active_program(self):
        self.client.login(username="mentor_user", password="password123")  # nosec B106
        url = reverse("program_detail", args=[self.active_program.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_mentor_cannot_view_past_program(self):
        self.client.login(username="mentor_user", password="password123")  # nosec B106
        url = reverse("program_detail", args=[self.past_program.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("home"))

    def test_mentor_cannot_view_upcoming_program(self):
        self.client.login(username="mentor_user", password="password123")  # nosec B106
        url = reverse("program_detail", args=[self.upcoming_program.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("home"))

    def test_mentor_cannot_view_inactive_program(self):
        self.client.login(username="mentor_user", password="password123")  # nosec B106
        url = reverse("program_detail", args=[self.inactive_program.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("home"))

    def test_lead_mentor_can_view_all_programs(self):
        self.client.login(username="lead_mentor", password="password123")  # nosec B106
        for prog in [
            self.active_program,
            self.past_program,
            self.upcoming_program,
            self.inactive_program,
        ]:
            url = reverse("program_detail", args=[prog.pk])
            response = self.client.get(url)
            self.assertEqual(
                response.status_code, 200, f"Lead mentor should view {prog.name}"
            )

    def test_mentor_restricted_actions_in_context(self):
        self.client.login(username="mentor_user", password="password123")  # nosec B106
        url = reverse("program_detail", args=[self.active_program.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context.get("can_add_payment"))
        self.assertFalse(response.context.get("can_view_payments"))
        self.assertFalse(response.context.get("can_view_attendance"))
        self.assertFalse(response.context.get("can_manage_fees"))
        self.assertFalse(response.context.get("can_manage_students"))

    def test_program_list_filtered_for_mentor(self):
        self.client.login(username="mentor_user", password="password123")  # nosec B106
        url = reverse("program_list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        programs = response.context["programs"]
        self.assertIn(self.active_program, programs)
        self.assertNotIn(self.past_program, programs)
        self.assertNotIn(self.upcoming_program, programs)
        self.assertNotIn(self.inactive_program, programs)

    def test_program_list_empty_for_student(self):
        self.client.login(username="student_user", password="password123")  # nosec B106
        url = reverse("program_list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["programs"]), 0)


class FinancePermissionTests(TestCase):
    def setUp(self):
        self.lead_mentor_user = User.objects.create_user(
            username="lead_mentor", password="password123"
        )  # nosec B106
        lm_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.lead_mentor_user.groups.add(lm_group)
        self.mentor_user = User.objects.create_user(
            username="mentor_user", password="password123"
        )  # nosec B106
        Adult.objects.create(
            user=self.mentor_user, first_name="Mentor", last_name="User", is_mentor=True
        )
        self.parent_user = User.objects.create_user(
            username="parent_user", password="password123"
        )  # nosec B106
        self.parent_adult = Adult.objects.create(
            user=self.parent_user, first_name="Parent", last_name="User", is_parent=True
        )
        self.student_user = User.objects.create_user(
            username="student_user", password="password123"
        )  # nosec B106
        self.student_profile = Student.objects.create(
            user=self.student_user, first_name="Student", last_name="User"
        )
        self.alumni_user = User.objects.create_user(
            username="alumni_user", password="password123"
        )  # nosec B106
        Adult.objects.create(
            user=self.alumni_user, first_name="Alumni", last_name="User", is_alumni=True
        )
        self.program = Program.objects.create(name="Test Program", active=True)
        Enrollment.objects.create(
            student=self.student_profile, program=self.program, active=True
        )
        AdultStudentRelationship.objects.create(
            adult=self.parent_adult,
            student=self.student_profile,
            relationship_to_student="parent",
        )

    def test_mentor_cannot_view_balance_sheet(self):
        self.client.login(username="mentor_user", password="password123")  # nosec B106
        url = reverse(
            "program_student_balance", args=[self.program.pk, self.student_profile.pk]
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

    def test_student_cannot_view_own_balance_sheet(self):
        self.client.login(username="student_user", password="password123")  # nosec B106
        url = reverse(
            "program_student_balance", args=[self.program.pk, self.student_profile.pk]
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

    def test_alumni_cannot_view_balance_sheet(self):
        self.client.login(username="alumni_user", password="password123")  # nosec B106
        url = reverse(
            "program_student_balance", args=[self.program.pk, self.student_profile.pk]
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

    def test_parent_can_view_child_balance_sheet(self):
        self.client.login(username="parent_user", password="password123")  # nosec B106
        url = reverse(
            "program_student_balance", args=[self.program.pk, self.student_profile.pk]
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_parent_cannot_view_other_student_balance_sheet(self):
        other_student = Student.objects.create(first_name="Other", last_name="Student")
        self.client.login(username="parent_user", password="password123")  # nosec B106
        url = reverse(
            "program_student_balance", args=[self.program.pk, other_student.pk]
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

    def test_parent_mentor_can_view_child_balance_sheet(self):
        """A parent who is also a mentor keeps parent finance access."""
        parent_mentor_user = User.objects.create_user(
            username="parent_mentor_user", password="password123"
        )  # nosec B106
        parent_mentor = Adult.objects.create(
            user=parent_mentor_user,
            first_name="Parent",
            last_name="Mentor",
            is_parent=True,
            is_mentor=True,
        )
        AdultStudentRelationship.objects.create(
            adult=parent_mentor,
            student=self.student_profile,
            relationship_to_student="parent",
        )
        self.client.login(
            username="parent_mentor_user", password="password123"
        )  # nosec B106
        url = reverse(
            "program_student_balance", args=[self.program.pk, self.student_profile.pk]
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_parent_mentor_cannot_view_other_student_balance_sheet(self):
        """A parent+mentor must still be scoped to their own students for finance."""
        parent_mentor_user = User.objects.create_user(
            username="parent_mentor_user2", password="password123"
        )  # nosec B106
        Adult.objects.create(
            user=parent_mentor_user,
            first_name="Parent",
            last_name="Mentor",
            is_parent=True,
            is_mentor=True,
        )
        other_student = Student.objects.create(first_name="Other", last_name="Student")
        self.client.login(
            username="parent_mentor_user2", password="password123"
        )  # nosec B106
        url = reverse(
            "program_student_balance", args=[self.program.pk, other_student.pk]
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

    def test_parent_mentor_can_read_sliding_scale(self):
        """Parent+mentor can read sliding scale (used on the Payments page)."""
        from programs.permission_views import can_user_read

        user = User.objects.create_user(
            username="parent_mentor_user3", password="password123"
        )  # nosec B106
        Adult.objects.create(
            user=user,
            first_name="Parent",
            last_name="Mentor",
            is_parent=True,
            is_mentor=True,
        )
        self.assertTrue(can_user_read(user, "sliding_scale"))
        self.assertTrue(can_user_read(user, "payments"))

    def test_mentor_cannot_view_payments_create(self):
        self.client.login(username="mentor_user", password="password123")  # nosec B106
        url = reverse("program_payment_create", args=[self.program.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

    def test_mentor_cannot_view_dues_owed(self):
        self.client.login(username="mentor_user", password="password123")  # nosec B106
        url = reverse("program_dues_owed", args=[self.program.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

    def test_mentor_cannot_view_email_balances(self):
        self.client.login(username="mentor_user", password="password123")  # nosec B106
        url = reverse("program_dues_email", args=[self.program.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)


class MentorAdultAccessTests(TestCase):
    """Mentors should only see Adults who are parents with a student in an active program."""

    def setUp(self):
        self.mentor_user = User.objects.create_user(
            username="mentor_user", password="password123"  # nosec B106
        )
        self.mentor = Adult.objects.create(
            user=self.mentor_user,
            first_name="Mentor",
            last_name="User",
            is_mentor=True,
        )
        RolePermission.objects.update_or_create(
            role="Mentor",
            section="adult_info",
            defaults={"can_read": True, "can_write": False},
        )
        self.active_program = Program.objects.create(name="Active Program", active=True)
        self.inactive_program = Program.objects.create(
            name="Inactive Program", active=False
        )
        self.parent_with_active = Adult.objects.create(
            first_name="Parent",
            last_name="Active",
            is_parent=True,
        )
        self.student_active = Student.objects.create(
            first_name="Active", last_name="Student"
        )
        self.parent_with_active.students.add(self.student_active)
        Enrollment.objects.create(
            student=self.student_active, program=self.active_program
        )
        self.parent_with_inactive = Adult.objects.create(
            first_name="Parent",
            last_name="Inactive",
            is_parent=True,
        )
        self.student_inactive = Student.objects.create(
            first_name="Inactive", last_name="Student"
        )
        self.parent_with_inactive.students.add(self.student_inactive)
        Enrollment.objects.create(
            student=self.student_inactive, program=self.inactive_program
        )
        self.non_parent_adult = Adult.objects.create(
            first_name="Just",
            last_name="Mentor",
            is_mentor=True,
        )

    def test_queryset_includes_parent_with_active_program_student(self):
        self.client.login(username="mentor_user", password="password123")  # nosec B106
        response = self.client.get(reverse("adult_list"))
        self.assertEqual(response.status_code, 200)
        adults = list(response.context["adults"])
        self.assertIn(self.parent_with_active, adults)

    def test_queryset_excludes_parent_without_active_program_student(self):
        self.client.login(username="mentor_user", password="password123")  # nosec B106
        response = self.client.get(reverse("adult_list"))
        adults = list(response.context["adults"])
        self.assertNotIn(self.parent_with_inactive, adults)

    def test_queryset_excludes_non_parent_adult(self):
        self.client.login(username="mentor_user", password="password123")  # nosec B106
        response = self.client.get(reverse("adult_list"))
        adults = list(response.context["adults"])
        self.assertNotIn(self.non_parent_adult, adults)

    def test_object_level_read_allowed_for_parent_with_active_program(self):
        self.assertTrue(
            can_user_read(self.mentor_user, "adult_info", obj=self.parent_with_active)
        )

    def test_object_level_read_denied_for_parent_without_active_program(self):
        self.assertFalse(
            can_user_read(self.mentor_user, "adult_info", obj=self.parent_with_inactive)
        )

    def test_object_level_read_denied_for_non_parent_adult(self):
        self.assertFalse(
            can_user_read(self.mentor_user, "adult_info", obj=self.non_parent_adult)
        )


class RoleProtectionTests(TestCase):
    def setUp(self):
        self.mentor_user = User.objects.create_user(
            username="mentor", password="pass"
        )  # nosec B106
        self.mentor_profile = Adult.objects.create(
            user=self.mentor_user,
            first_name="Regular",
            last_name="Mentor",
            is_mentor=True,
        )
        perm = Permission.objects.get(codename="change_adult")
        self.mentor_user.user_permissions.add(perm)
        self.lead_user = User.objects.create_superuser(
            username="lead", password="pass"
        )  # nosec B106
        RolePermission.objects.update_or_create(
            role="Mentor",
            section="adult_info",
            defaults={"can_read": True, "can_write": True},
        )
        self.client.login(username="mentor", password="pass")  # nosec B106

    def test_mentor_cannot_uncheck_is_mentor_flag(self):
        url = reverse("adult_edit", args=[self.mentor_profile.pk])
        data = {
            "first_name": "Regular",
            "last_name": "Mentor Updated",
            "personal_email": "mentor@example.com",
        }
        self.client.login(username="mentor", password="pass")  # nosec B106
        resp = self.client.post(url, data)
        self.assertEqual(resp.status_code, 302)
        self.mentor_profile.refresh_from_db()
        self.assertEqual(self.mentor_profile.last_name, "Mentor Updated")
        self.assertTrue(self.mentor_profile.is_mentor)
        self.assertTrue(self.mentor_profile.active)

    def test_parent_cannot_change_students(self):
        parent_user = User.objects.create_user(
            username="parent_user", password="pass"
        )  # nosec B106
        parent_profile = Adult.objects.create(
            user=parent_user, first_name="Parent", last_name="User", is_parent=True
        )
        perm = Permission.objects.get(codename="change_adult")
        parent_user.user_permissions.add(perm)
        RolePermission.objects.update_or_create(
            role="Parent",
            section="adult_info",
            defaults={"can_read": True, "can_write": True},
        )
        self.client.login(username="parent_user", password="pass")  # nosec B106
        url = reverse("parent_edit", args=[parent_profile.pk])
        data = {
            "first_name": "Parent",
            "last_name": "Updated",
            "personal_email": "parent@example.com",
            "students": [1, 2, 3],
        }
        resp = self.client.post(url, data)
        self.assertEqual(resp.status_code, 302)
        parent_profile.refresh_from_db()
        self.assertEqual(parent_profile.last_name, "Updated")
        self.assertEqual(parent_profile.students.count(), 0)

    def test_lead_mentor_CAN_change_flags(self):
        self.client.login(username="lead", password="pass")  # nosec B106
        url = reverse("adult_edit", args=[self.mentor_profile.pk])
        data = {
            "first_name": "Regular",
            "last_name": "Mentor",
            "personal_email": "mentor@example.com",
            "is_mentor": "on",
            "is_parent": "on",
            "active": "on",
        }
        resp = self.client.post(url, data)
        self.assertEqual(resp.status_code, 302)
        self.mentor_profile.refresh_from_db()
        self.assertTrue(self.mentor_profile.is_parent)


class PortalPermissionsUpdateTests(TestCase):
    def setUp(self):
        self.password = "password123"  # nosec B105
        self.lead_mentor = User.objects.create_user(
            username="lead_mentor_user", password=self.password
        )
        self.lead_mentor_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.lead_mentor.groups.add(self.lead_mentor_group)
        self.mentor_user = User.objects.create_user(
            username="mentor_user_perm", password=self.password
        )
        Adult.objects.create(
            user=self.mentor_user, first_name="Mentor", last_name="User", is_mentor=True
        )
        self.mentor_group, _ = Group.objects.get_or_create(name="Mentor")
        self.mentor_user.groups.add(self.mentor_group)
        self.url = reverse("portal_permissions_update")

    def test_lead_mentor_can_update_permissions(self):
        self.client.login(username="lead_mentor_user", password=self.password)
        perm, _ = RolePermission.objects.get_or_create(
            role="Mentor", section="attendance"
        )
        perm.can_read = False
        perm.can_write = False
        perm.save()
        response = self.client.post(
            self.url,
            {f"read_{perm.id}": "on"},
        )
        self.assertEqual(response.status_code, 302)
        perm.refresh_from_db()
        self.assertTrue(perm.can_read)
        self.assertFalse(perm.can_write)

        response = self.client.post(
            self.url,
            {
                f"read_{perm.id}": "on",
                f"write_{perm.id}": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        perm.refresh_from_db()
        self.assertTrue(perm.can_read)
        self.assertTrue(perm.can_write)

    def test_non_lead_mentor_cannot_update_permissions(self):
        self.client.login(username="mentor_user_perm", password=self.password)
        perm, _ = RolePermission.objects.get_or_create(
            role="Mentor", section="attendance"
        )
        perm.can_read = False
        perm.save()
        response = self.client.post(
            self.url,
            {f"read_{perm.id}": "on"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("home"))
        perm.refresh_from_db()
        self.assertFalse(perm.can_read)


class GetUserRoleTests(TestCase):
    def test_superuser_is_lead_mentor(self):
        from programs.permission_views import get_user_role

        user = User.objects.create_superuser(
            username="admin", password="password123"
        )  # nosec B106
        self.assertEqual(get_user_role(user), "LeadMentor")

    def test_lead_mentor_group_is_lead_mentor(self):
        from programs.permission_views import get_user_role

        user = User.objects.create_user(
            username="lm", password="password123"
        )  # nosec B106
        lm_group, _ = Group.objects.get_or_create(name="LeadMentor")
        user.groups.add(lm_group)
        self.assertEqual(get_user_role(user), "LeadMentor")

    def test_mentor_priority_over_parent_and_alumni(self):
        from programs.permission_views import get_user_role

        user = User.objects.create_user(
            username="mentor_parent_alumni", password="password123"
        )  # nosec B106
        Adult.objects.create(
            user=user,
            first_name="Test",
            last_name="User",
            is_mentor=True,
            is_parent=True,
            is_alumni=True,
        )
        self.assertEqual(get_user_role(user), "Mentor")

    def test_parent_priority_over_alumni(self):
        from programs.permission_views import get_user_role

        user = User.objects.create_user(
            username="parent_alumni", password="password123"
        )  # nosec B106
        Adult.objects.create(
            user=user,
            first_name="Test",
            last_name="User",
            is_mentor=False,
            is_parent=True,
            is_alumni=True,
        )
        self.assertEqual(get_user_role(user), "Parent")

    def test_alumni_role(self):
        from programs.permission_views import get_user_role

        user = User.objects.create_user(
            username="alumni_only", password="password123"
        )  # nosec B106
        Adult.objects.create(
            user=user,
            first_name="Test",
            last_name="User",
            is_mentor=False,
            is_parent=False,
            is_alumni=True,
        )
        self.assertEqual(get_user_role(user), "Alumni")

    def test_student_profile_role(self):
        from programs.permission_views import get_user_role

        user = User.objects.create_user(
            username="stu", password="password123"
        )  # nosec B106
        Student.objects.create(user=user, first_name="Student", last_name="User")
        self.assertEqual(get_user_role(user), "Student")

    def test_lead_mentor_group_overrides_profiles(self):
        from programs.permission_views import get_user_role

        user = User.objects.create_user(
            username="lm_with_profile", password="password123"
        )  # nosec B106
        Adult.objects.create(
            user=user, first_name="Test", last_name="User", is_mentor=True
        )
        lm_group, _ = Group.objects.get_or_create(name="LeadMentor")
        user.groups.add(lm_group)
        self.assertEqual(get_user_role(user), "LeadMentor")

    def test_no_profile_or_group_returns_none(self):
        from programs.permission_views import get_user_role

        user = User.objects.create_user(
            username="nobody", password="password123"
        )  # nosec B106
        self.assertIsNone(get_user_role(user))

    def test_group_fallback_mentor(self):
        from programs.permission_views import get_user_role

        user = User.objects.create_user(
            username="mentor_group", password="password123"
        )  # nosec B106
        group, _ = Group.objects.get_or_create(name="Mentor")
        user.groups.add(group)
        self.assertEqual(get_user_role(user), "Mentor")

    def test_group_fallback_parent(self):
        from programs.permission_views import get_user_role

        user = User.objects.create_user(
            username="parent_group", password="password123"
        )  # nosec B106
        group, _ = Group.objects.get_or_create(name="Parent")
        user.groups.add(group)
        self.assertEqual(get_user_role(user), "Parent")

    def test_group_fallback_student(self):
        from programs.permission_views import get_user_role

        user = User.objects.create_user(
            username="student_group", password="password123"
        )  # nosec B106
        group, _ = Group.objects.get_or_create(name="Student")
        user.groups.add(group)
        self.assertEqual(get_user_role(user), "Student")


class UserRoleFlagTests(TestCase):
    """user_is_parent / user_is_mentor / user_is_alumni helpers.

    These check a single Adult role flag independently of role priority, so a
    parent who also mentors (or is an alumni) is still recognized as a parent.
    """

    def test_parent_only(self):
        from programs.permission_views import (
            user_is_alumni,
            user_is_mentor,
            user_is_parent,
        )

        user = User.objects.create_user(
            username="parent_only", password="password123"
        )  # nosec B106
        Adult.objects.create(
            user=user, first_name="Test", last_name="User", is_parent=True
        )
        self.assertTrue(user_is_parent(user))
        self.assertFalse(user_is_mentor(user))
        self.assertFalse(user_is_alumni(user))

    def test_parent_and_mentor(self):
        from programs.permission_views import (
            user_is_alumni,
            user_is_mentor,
            user_is_parent,
        )

        user = User.objects.create_user(
            username="parent_mentor", password="password123"
        )  # nosec B106
        Adult.objects.create(
            user=user,
            first_name="Test",
            last_name="User",
            is_parent=True,
            is_mentor=True,
        )
        self.assertTrue(user_is_parent(user))
        self.assertTrue(user_is_mentor(user))
        self.assertFalse(user_is_alumni(user))

    def test_all_three_flags(self):
        from programs.permission_views import (
            user_is_alumni,
            user_is_mentor,
            user_is_parent,
        )

        user = User.objects.create_user(
            username="all_three", password="password123"
        )  # nosec B106
        Adult.objects.create(
            user=user,
            first_name="Test",
            last_name="User",
            is_parent=True,
            is_mentor=True,
            is_alumni=True,
        )
        self.assertTrue(user_is_parent(user))
        self.assertTrue(user_is_mentor(user))
        self.assertTrue(user_is_alumni(user))

    def test_mentor_only(self):
        from programs.permission_views import user_is_parent

        user = User.objects.create_user(
            username="mentor_only", password="password123"
        )  # nosec B106
        Adult.objects.create(
            user=user, first_name="Test", last_name="User", is_mentor=True
        )
        self.assertFalse(user_is_parent(user))

    def test_anonymous_and_unlinked_users(self):
        from django.contrib.auth.models import AnonymousUser

        from programs.permission_views import (
            user_is_alumni,
            user_is_mentor,
            user_is_parent,
        )

        self.assertFalse(user_is_parent(AnonymousUser()))
        self.assertFalse(user_is_mentor(AnonymousUser()))
        self.assertFalse(user_is_alumni(AnonymousUser()))

        user = User.objects.create_user(
            username="nobody", password="password123"
        )  # nosec B106
        self.assertFalse(user_is_parent(user))
        self.assertFalse(user_is_mentor(user))
        self.assertFalse(user_is_alumni(user))

    def test_parent_group_fallback(self):
        from programs.permission_views import user_is_parent

        user = User.objects.create_user(
            username="parent_group", password="password123"
        )  # nosec B106
        group, _ = Group.objects.get_or_create(name="Parent")
        user.groups.add(group)
        self.assertTrue(user_is_parent(user))

    def test_mentor_group_fallback(self):
        from programs.permission_views import user_is_mentor

        user = User.objects.create_user(
            username="mentor_group", password="password123"
        )  # nosec B106
        group, _ = Group.objects.get_or_create(name="Mentor")
        user.groups.add(group)
        self.assertTrue(user_is_mentor(user))


class SignalGroupAssignmentTests(TestCase):
    """Tests that post_save receivers for Adult and Student fire correctly."""

    def test_mentor_adult_added_to_mentor_group_on_save(self):
        user = User.objects.create_user(
            username="mentor1", password="password"
        )  # nosec B106
        adult = Adult.objects.create(
            first_name="Mel",
            last_name="Mentor",
            is_mentor=True,
            user=user,
        )
        adult.save()
        user.refresh_from_db()
        self.assertTrue(user.groups.filter(name="Mentor").exists())

    def test_parent_adult_added_to_parent_group_on_save(self):
        user = User.objects.create_user(
            username="parent1", password="password"
        )  # nosec B106
        adult = Adult.objects.create(
            first_name="Pat",
            last_name="Parent",
            is_parent=True,
            user=user,
        )
        adult.save()
        user.refresh_from_db()
        self.assertTrue(user.groups.filter(name="Parent").exists())

    def test_student_with_user_added_to_student_group_on_save(self):
        user = User.objects.create_user(
            username="student1", password="password"
        )  # nosec B106
        student = Student.objects.create(
            legal_first_name="Sam",
            last_name="Student",
            user=user,
        )
        student.save()
        user.refresh_from_db()
        self.assertTrue(user.groups.filter(name="Student").exists())
