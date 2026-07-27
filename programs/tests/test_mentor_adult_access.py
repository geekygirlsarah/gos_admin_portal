from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from programs.models import (
    Adult,
    Enrollment,
    Program,
    RolePermission,
    Student,
)
from programs.permission_views import can_user_read


class MentorAdultAccessTests(TestCase):
    """TDD for Issue 9: Mentors should only see Adults who are parents
    (is_parent=True) and have at least one student enrolled in an active
    program.
    """

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

        # Parent with a student in an active program
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

        # Parent with a student only in an inactive program
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

        # Non-parent adult (mentor-only, not a parent)
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
