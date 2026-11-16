from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from attendance.models import AttendanceSession
from organizations.models import Organization, UserOrganizationMembership
from organizations.utils import get_default_organization
from programs.models import (
    Adult,
    Enrollment,
    Program,
    ProgramFeature,
    RolePermission,
    Student,
)
from programs.permission_views import (
    can_user_delete,
    can_user_read,
    can_user_write,
    user_is_lead_mentor,
)

from .base import make_lead_mentor_user, make_program


def _make_program(name="Test Program", active=True):
    prog = Program.objects.create(name=name, active=active)
    feat, _ = ProgramFeature.objects.get_or_create(
        key="attendance", defaults={"name": "Attendance"}
    )
    prog.features.add(feat)
    return prog


class MentorAttendanceReadPermissionTests(TestCase):
    def setUp(self):
        self.active_program = _make_program("Active Program", active=True)
        self.inactive_program = _make_program("Inactive Program", active=False)

        self.mentor_user = User.objects.create_user(
            username="mentor_perm", password="password123"  # nosec B106
        )
        Adult.objects.create(
            user=self.mentor_user,
            legal_first_name="Mentor",
            last_name="User",
            is_mentor=True,
        )

        self.student = Student.objects.create(
            preferred_first_name="Test", last_name="Student"
        )
        Enrollment.objects.create(student=self.student, program=self.active_program)

        RolePermission.objects.update_or_create(
            role="Mentor",
            section="attendance",
            defaults={"can_read": True, "can_write": True},
        )

    def test_mentor_can_read_student_attendance(self):
        self.assertTrue(
            can_user_read(
                self.mentor_user,
                "attendance",
                obj=self.student,
                org=self.student.organization,
            )
        )

    def test_mentor_cannot_read_attendance_when_role_permission_denies(self):
        RolePermission.objects.update_or_create(
            role="Mentor",
            section="attendance",
            defaults={"can_read": False, "can_write": False},
        )
        self.assertFalse(
            can_user_read(
                self.mentor_user,
                "attendance",
                obj=self.student,
                org=self.student.organization,
            )
        )


class MentorAttendanceWritePermissionTests(TestCase):
    def setUp(self):
        self.program = _make_program()

        self.mentor_user = User.objects.create_user(
            username="mentor_write", password="password123"  # nosec B106
        )
        Adult.objects.create(
            user=self.mentor_user,
            legal_first_name="Mentor",
            last_name="Writer",
            is_mentor=True,
        )

        self.student = Student.objects.create(
            preferred_first_name="Write", last_name="Student"
        )

        RolePermission.objects.update_or_create(
            role="Mentor",
            section="attendance",
            defaults={"can_read": True, "can_write": True},
        )

    def test_mentor_can_write_attendance(self):
        self.assertTrue(
            can_user_write(
                self.mentor_user, "attendance", org=self.student.organization
            )
        )

    def test_mentor_cannot_delete_attendance(self):
        self.assertFalse(
            can_user_delete(
                self.mentor_user, "attendance", org=self.student.organization
            )
        )


class MentorAttendanceDeleteViewTests(TestCase):
    def setUp(self):
        self.program = _make_program()

        self.mentor_user = User.objects.create_user(
            username="mentor_delete", password="password123"  # nosec B106
        )
        Adult.objects.create(
            user=self.mentor_user,
            legal_first_name="Mentor",
            last_name="Deleter",
            is_mentor=True,
        )

        self.student = Student.objects.create(
            preferred_first_name="Del", last_name="Student"
        )

        self.session = AttendanceSession.objects.create(
            program=self.program,
            student=self.student,
            check_in=timezone.now(),
        )

        RolePermission.objects.update_or_create(
            role="Mentor",
            section="attendance",
            defaults={"can_read": True, "can_write": True},
        )

    def test_mentor_cannot_delete_session_via_view(self):
        self.client.login(
            username="mentor_delete", password="password123"
        )  # nosec B106
        url = reverse("student_attendance", args=[self.student.pk])
        response = self.client.post(
            url,
            {
                "action": "delete",
                "session_id": self.session.pk,
            },
        )
        self.assertTrue(AttendanceSession.objects.filter(pk=self.session.pk).exists())
        self.assertIn(response.status_code, [302, 403])


class StudentAttendancePermissionTests(TestCase):
    def setUp(self):
        self.student_user = User.objects.create_user(
            username="student_own", password="password123"  # nosec B106
        )
        self.student = Student.objects.create(
            user=self.student_user, preferred_first_name="Own", last_name="Student"
        )
        self.other_student = Student.objects.create(
            preferred_first_name="Other", last_name="Student"
        )

    def test_student_can_read_own_attendance(self):
        self.assertTrue(
            can_user_read(
                self.student_user,
                "attendance",
                obj=self.student,
                org=self.student.organization,
            )
        )

    def test_student_cannot_read_other_attendance(self):
        self.assertFalse(
            can_user_read(
                self.student_user,
                "attendance",
                obj=self.other_student,
                org=self.student.organization,
            )
        )

    def test_student_cannot_delete_attendance(self):
        self.assertFalse(
            can_user_delete(
                self.student_user, "attendance", org=self.student.organization
            )
        )


class ParentAttendancePermissionTests(TestCase):
    def setUp(self):
        self.parent_user = User.objects.create_user(
            username="parent_att", password="password123"  # nosec B106
        )
        self.parent = Adult.objects.create(
            user=self.parent_user,
            legal_first_name="Parent",
            last_name="User",
            is_parent=True,
        )
        self.child = Student.objects.create(
            preferred_first_name="Child", last_name="Student"
        )
        self.parent.students.add(self.child)

        self.other_student = Student.objects.create(
            preferred_first_name="Other", last_name="Child"
        )

    def test_parent_can_read_own_child_attendance(self):
        self.assertTrue(
            can_user_read(
                self.parent_user,
                "attendance",
                obj=self.child,
                org=self.child.organization,
            )
        )

    def test_parent_cannot_read_other_student_attendance(self):
        self.assertFalse(
            can_user_read(
                self.parent_user,
                "attendance",
                obj=self.other_student,
                org=self.child.organization,
            )
        )

    def test_parent_cannot_delete_attendance(self):
        self.assertFalse(
            can_user_delete(self.parent_user, "attendance", org=self.child.organization)
        )


class VisitorAttendancePermissionTests(TestCase):
    def setUp(self):
        self.visitor_user = User.objects.create_user(
            username="visitor_att", password="password123"  # nosec B106
        )
        self.student = Student.objects.create(
            preferred_first_name="Any", last_name="Student"
        )

    def test_visitor_cannot_read_attendance(self):
        self.assertFalse(
            can_user_read(
                self.visitor_user,
                "attendance",
                obj=self.student,
                org=self.student.organization,
            )
        )

    def test_visitor_cannot_read_attendance_section(self):
        self.assertFalse(
            can_user_read(
                self.visitor_user, "attendance", org=self.student.organization
            )
        )

    def test_visitor_cannot_delete_attendance(self):
        self.assertFalse(
            can_user_delete(
                self.visitor_user, "attendance", org=self.student.organization
            )
        )


class LeadMentorAttendancePermissionTests(TestCase):
    def setUp(self):
        self.lead_user = make_lead_mentor_user(
            username="lead_att", password="password123"  # nosec B106
        )

        self.student = Student.objects.create(
            preferred_first_name="Any", last_name="Student"
        )

    def test_lead_mentor_can_read_attendance(self):
        self.assertTrue(
            can_user_read(
                self.lead_user,
                "attendance",
                obj=self.student,
                org=self.student.organization,
            )
        )

    def test_lead_mentor_can_write_attendance(self):
        self.assertTrue(
            can_user_write(self.lead_user, "attendance", org=self.student.organization)
        )

    def test_lead_mentor_can_delete_attendance(self):
        self.assertTrue(
            can_user_delete(self.lead_user, "attendance", org=self.student.organization)
        )


class LeadMentorFixtureTests(TestCase):
    def test_lead_fixture_has_active_default_organization_membership(self):
        lead = make_lead_mentor_user()
        organization = get_default_organization()

        self.assertIsNotNone(organization)
        self.assertTrue(
            UserOrganizationMembership.objects.filter(
                user=lead,
                organization=organization,
                role="LeadMentor",
                is_active=True,
            ).exists()
        )
        self.assertTrue(user_is_lead_mentor(lead, org=organization))

    def test_lead_fixture_does_not_grant_lead_role_in_another_organization(self):
        lead = make_lead_mentor_user()
        other = Organization.objects.create(
            name="Other Organization", slug="other", subdomain="other"
        )

        self.assertFalse(user_is_lead_mentor(lead, org=other))
