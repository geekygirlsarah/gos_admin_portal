"""
TDD for attendance permission rules (Issue: permission structures for attendance data).

Permission spec:
- Students: Can view their own attendance data, cannot view other attendance data.
- Parents: Can view their students' attendance data, cannot view other attendance data.
- Mentors: Can add or edit student attendance data for current programs, cannot delete
           student attendance data, can view student attendance (to add/edit), cannot
           view any other attendance data they don't have access to.
- Visitors (no role): Cannot view any attendance data.
- Lead Mentors: Full admin access.
"""

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from programs.models import (
    Adult,
    Enrollment,
    Program,
    ProgramFeature,
    RolePermission,
    Student,
)
from programs.permission_views import can_user_delete, can_user_read, can_user_write

from .models import AttendanceSession


def _make_program(name="Test Program", active=True):
    prog = Program.objects.create(name=name, active=active)
    feat, _ = ProgramFeature.objects.get_or_create(
        key="attendance", defaults={"name": "Attendance"}
    )
    prog.features.add(feat)
    return prog


class MentorAttendanceReadPermissionTests(TestCase):
    """Mentors can read student attendance for current programs."""

    def setUp(self):
        self.active_program = _make_program("Active Program", active=True)
        self.inactive_program = _make_program("Inactive Program", active=False)

        self.mentor_user = User.objects.create_user(
            username="mentor_perm", password="password123"  # nosec B106
        )
        self.mentor = Adult.objects.create(
            user=self.mentor_user,
            first_name="Mentor",
            last_name="User",
            is_mentor=True,
        )

        self.student = Student.objects.create(first_name="Test", last_name="Student")
        Enrollment.objects.create(student=self.student, program=self.active_program)

        # Ensure RolePermission allows mentor to read attendance
        RolePermission.objects.update_or_create(
            role="Mentor",
            section="attendance",
            defaults={"can_read": True, "can_write": True},
        )

    def test_mentor_can_read_student_attendance(self):
        """Mentors can view student attendance data (to add/edit it)."""
        self.assertTrue(can_user_read(self.mentor_user, "attendance", obj=self.student))

    def test_mentor_cannot_read_attendance_when_role_permission_denies(self):
        """If the dynamic RolePermission denies read, Mentor cannot read."""
        RolePermission.objects.update_or_create(
            role="Mentor",
            section="attendance",
            defaults={"can_read": False, "can_write": False},
        )
        self.assertFalse(
            can_user_read(self.mentor_user, "attendance", obj=self.student)
        )


class MentorAttendanceWritePermissionTests(TestCase):
    """Mentors can add/edit but NOT delete student attendance."""

    def setUp(self):
        self.program = _make_program()

        self.mentor_user = User.objects.create_user(
            username="mentor_write", password="password123"  # nosec B106
        )
        Adult.objects.create(
            user=self.mentor_user,
            first_name="Mentor",
            last_name="Writer",
            is_mentor=True,
        )

        self.student = Student.objects.create(first_name="Write", last_name="Student")

        RolePermission.objects.update_or_create(
            role="Mentor",
            section="attendance",
            defaults={"can_read": True, "can_write": True},
        )

    def test_mentor_can_write_attendance(self):
        """Mentors with write permission can create/edit attendance."""
        self.assertTrue(can_user_write(self.mentor_user, "attendance"))

    def test_mentor_cannot_delete_attendance(self):
        """Mentors cannot delete student attendance records."""
        self.assertFalse(can_user_delete(self.mentor_user, "attendance"))


class MentorAttendanceDeleteViewTests(TestCase):
    """Mentors are blocked from the delete action on the student attendance view."""

    def setUp(self):
        self.program = _make_program()

        self.mentor_user = User.objects.create_user(
            username="mentor_delete", password="password123"  # nosec B106
        )
        Adult.objects.create(
            user=self.mentor_user,
            first_name="Mentor",
            last_name="Deleter",
            is_mentor=True,
        )

        self.student = Student.objects.create(first_name="Del", last_name="Student")

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
        """POST delete action from a Mentor should return 403 or redirect with error."""
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
        # Session must still exist
        self.assertTrue(AttendanceSession.objects.filter(pk=self.session.pk).exists())
        # Should either redirect or return 403
        self.assertIn(response.status_code, [302, 403])


class StudentAttendancePermissionTests(TestCase):
    """Students can view their own attendance only."""

    def setUp(self):
        self.student_user = User.objects.create_user(
            username="student_own", password="password123"  # nosec B106
        )
        self.student = Student.objects.create(
            user=self.student_user, first_name="Own", last_name="Student"
        )
        self.other_student = Student.objects.create(
            first_name="Other", last_name="Student"
        )

    def test_student_can_read_own_attendance(self):
        self.assertTrue(
            can_user_read(self.student_user, "attendance", obj=self.student)
        )

    def test_student_cannot_read_other_attendance(self):
        self.assertFalse(
            can_user_read(self.student_user, "attendance", obj=self.other_student)
        )

    def test_student_cannot_delete_attendance(self):
        self.assertFalse(can_user_delete(self.student_user, "attendance"))


class ParentAttendancePermissionTests(TestCase):
    """Parents can view only their students' attendance data."""

    def setUp(self):
        self.parent_user = User.objects.create_user(
            username="parent_att", password="password123"  # nosec B106
        )
        self.parent = Adult.objects.create(
            user=self.parent_user,
            first_name="Parent",
            last_name="User",
            is_parent=True,
        )
        self.child = Student.objects.create(first_name="Child", last_name="Student")
        self.parent.students.add(self.child)

        self.other_student = Student.objects.create(
            first_name="Other", last_name="Child"
        )

    def test_parent_can_read_own_child_attendance(self):
        self.assertTrue(can_user_read(self.parent_user, "attendance", obj=self.child))

    def test_parent_cannot_read_other_student_attendance(self):
        self.assertFalse(
            can_user_read(self.parent_user, "attendance", obj=self.other_student)
        )

    def test_parent_cannot_delete_attendance(self):
        self.assertFalse(can_user_delete(self.parent_user, "attendance"))


class VisitorAttendancePermissionTests(TestCase):
    """Users with no role (visitors) cannot view any attendance data."""

    def setUp(self):
        self.visitor_user = User.objects.create_user(
            username="visitor_att", password="password123"  # nosec B106
        )
        self.student = Student.objects.create(first_name="Any", last_name="Student")

    def test_visitor_cannot_read_attendance(self):
        self.assertFalse(
            can_user_read(self.visitor_user, "attendance", obj=self.student)
        )

    def test_visitor_cannot_read_attendance_section(self):
        self.assertFalse(can_user_read(self.visitor_user, "attendance"))

    def test_visitor_cannot_delete_attendance(self):
        self.assertFalse(can_user_delete(self.visitor_user, "attendance"))


class LeadMentorAttendancePermissionTests(TestCase):
    """Lead Mentors have full admin access to attendance."""

    def setUp(self):
        self.lead_user = User.objects.create_user(
            username="lead_att", password="password123"  # nosec B106
        )
        lm_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.lead_user.groups.add(lm_group)

        self.student = Student.objects.create(first_name="Any", last_name="Student")

    def test_lead_mentor_can_read_attendance(self):
        self.assertTrue(can_user_read(self.lead_user, "attendance", obj=self.student))

    def test_lead_mentor_can_write_attendance(self):
        self.assertTrue(can_user_write(self.lead_user, "attendance"))

    def test_lead_mentor_can_delete_attendance(self):
        self.assertTrue(can_user_delete(self.lead_user, "attendance"))
