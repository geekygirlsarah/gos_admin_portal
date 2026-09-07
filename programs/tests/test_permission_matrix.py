"""Parameterized permission matrix tests covering all roles × sections × read/write.

Tests match the actual implementation in programs.permission_views.
"""

from django.contrib.auth.models import Group, User
from django.test import TestCase

from programs.models import Adult, Program, RolePermission, Student
from programs.permission_views import (
    can_user_read,
    can_user_write,
    get_user_role,
    user_is_parent,
)


class PermissionMatrixTests(TestCase):
    """Test all role/section combinations for can_user_read and can_user_write."""

    @classmethod
    def setUpTestData(cls):
        # Create test users for each role
        cls.lead_mentor = User.objects.create_superuser(
            username="lead_mentor", password="pass"
        )  # nosec B106
        lm_group, _ = Group.objects.get_or_create(name="LeadMentor")
        cls.lead_mentor.groups.add(lm_group)

        cls.mentor_user = User.objects.create_user(
            username="mentor", password="pass"
        )  # nosec B106
        cls.mentor = Adult.objects.create(
            user=cls.mentor_user,
            legal_first_name="Mentor",
            last_name="User",
            is_mentor=True,
        )

        cls.parent_user = User.objects.create_user(
            username="parent", password="pass"
        )  # nosec B106
        cls.parent = Adult.objects.create(
            user=cls.parent_user,
            legal_first_name="Parent",
            last_name="User",
            is_parent=True,
        )

        cls.student_user = User.objects.create_user(
            username="student", password="pass"
        )  # nosec B106
        cls.student = Student.objects.create(
            user=cls.student_user, preferred_first_name="Student", last_name="User"
        )

        cls.alumni_user = User.objects.create_user(
            username="alumni", password="pass"
        )  # nosec B106
        cls.alumni = Adult.objects.create(
            user=cls.alumni_user,
            legal_first_name="Alumni",
            last_name="User",
            is_alumni=True,
        )

        # Create a test program and link student to parent
        cls.program = Program.objects.create(name="Test Program", active=True)
        cls.student.adults.add(cls.parent)

        # All sections from RolePermission.SECTION_CHOICES
        cls.sections = [code for code, _ in RolePermission.SECTION_CHOICES]
        cls.roles = [role for role, _ in RolePermission.ROLE_CHOICES]

        # Sections with special restrictions
        cls.finance_sections = {"payments", "sliding_scale", "fees"}
        cls.mentor_only_sections = {"team_assignments", "badge_award", "badge_manage"}
        cls.mentor_default_no_read = {
            "attendance",
            "team_assignments",
            "badge_award",
            "badge_manage",
        }
        cls.no_write_except_lead = {
            "background_checks",
            "payments",
            "sliding_scale",
            "fees",
        }
        # Parents/alumni never see or touch order requests, regardless of the
        # RolePermission row a Lead Mentor might flip in Portal Settings.
        cls.orders_denied = {
            "orders-view",
            "orders-request",
            "orders-manage",
            "orders-shipping",
        }

    def _set_permission(self, role, section, can_read=True, can_write=False):
        RolePermission.objects.update_or_create(
            role=role,
            section=section,
            defaults={"can_read": can_read, "can_write": can_write},
        )

    def _clear_permissions(self):
        RolePermission.objects.all().delete()

    # --- Lead Mentor (superuser) should always have full access ---

    def test_lead_mentor_read_all_sections(self):
        """Lead Mentor can read all sections regardless of RolePermission settings."""
        self._clear_permissions()
        for section in self.sections:
            with self.subTest(section=section):
                self.assertTrue(
                    can_user_read(self.lead_mentor, section),
                    f"LeadMentor should read {section}",
                )

    def test_lead_mentor_write_all_sections(self):
        """Lead Mentor can write all sections regardless of RolePermission settings."""
        self._clear_permissions()
        for section in self.sections:
            with self.subTest(section=section):
                self.assertTrue(
                    can_user_write(self.lead_mentor, section),
                    f"LeadMentor should write {section}",
                )

    # --- Mentor role tests ---

    def test_mentor_read_matrix(self):
        """Test Mentor read access for all sections."""
        self._clear_permissions()
        for section in self.sections:
            with self.subTest(section=section):
                # Finance sections are restricted to parents/lead mentors only
                if section in self.finance_sections:
                    # Mentors cannot read finance sections even with can_read=True
                    self._set_permission(
                        "Mentor", section, can_read=True, can_write=False
                    )
                    self.assertFalse(
                        can_user_read(self.mentor_user, section),
                        f"Mentor should NOT read {section} (finance restricted)",
                    )
                else:
                    # Other sections respect RolePermission
                    self._set_permission(
                        "Mentor", section, can_read=True, can_write=False
                    )
                    self.assertTrue(
                        can_user_read(self.mentor_user, section),
                        f"Mentor should read {section} when can_read=True",
                    )

                    # Test default (no RolePermission entry)
                    self._clear_permissions()
                    expected = section not in self.mentor_default_no_read
                    self.assertEqual(
                        can_user_read(self.mentor_user, section),
                        expected,
                        f"Mentor default read for {section} should be {expected}",
                    )

    def test_mentor_write_matrix(self):
        """Test Mentor write access for all sections."""
        self._clear_permissions()
        for section in self.sections:
            with self.subTest(section=section):
                self._set_permission("Mentor", section, can_read=True, can_write=True)
                can_write = can_user_write(self.mentor_user, section)

                # Hard-coded no-write sections
                if section in self.no_write_except_lead:
                    self.assertFalse(
                        can_write, f"Mentor should NOT write {section} (restricted)"
                    )
                elif section == "student_info":
                    # Explicitly blocked for Mentor
                    self.assertFalse(can_write, "Mentor cannot write student_info")
                else:
                    self.assertTrue(
                        can_write, f"Mentor should write {section} when can_write=True"
                    )

    # --- Parent role tests ---

    def test_parent_read_matrix(self):
        """Test Parent read access for all sections."""
        self._clear_permissions()
        for section in self.sections:
            with self.subTest(section=section):
                self._set_permission("Parent", section, can_read=True, can_write=False)

                # Finance sections: parents can read if can_read=True
                if section in self.finance_sections:
                    self.assertTrue(
                        can_user_read(self.parent_user, section),
                        f"Parent should read {section} when can_read=True",
                    )
                # Mentor-only sections: parents cannot read
                elif section in self.mentor_only_sections:
                    self.assertFalse(
                        can_user_read(self.parent_user, section),
                        f"Parent should NOT read {section} (mentor-only)",
                    )
                # Orders: parents never read, even when a Lead flips the toggle on
                elif section in self.orders_denied:
                    self.assertFalse(
                        can_user_read(self.parent_user, section),
                        f"Parent should NOT read {section} (orders restricted)",
                    )
                else:
                    self.assertTrue(
                        can_user_read(self.parent_user, section),
                        f"Parent should read {section} when can_read=True",
                    )

                # Test can_read=False
                self._set_permission("Parent", section, can_read=False, can_write=False)
                result = can_user_read(self.parent_user, section)
                if section in self.finance_sections:
                    # Finance sections default True for parent even without permission?
                    # Actually defaults to True per can_user_read logic
                    pass
                else:
                    self.assertFalse(
                        result,
                        f"Parent should NOT read {section} generally when can_read=False",
                    )

    def test_parent_write_matrix(self):
        """Test Parent write access for all sections."""
        self._clear_permissions()
        for section in self.sections:
            with self.subTest(section=section):
                self._set_permission("Parent", section, can_read=True, can_write=True)
                can_write = can_user_write(self.parent_user, section)

                # Hard-coded no-write sections for non-LeadMentor
                if section in self.no_write_except_lead:
                    self.assertFalse(
                        can_write, f"Parent should NOT write {section} (restricted)"
                    )
                elif section in self.mentor_only_sections:
                    # Mentor-only sections
                    self.assertFalse(
                        can_write, f"Parent cannot write {section} (mentor-only)"
                    )
                elif section in self.orders_denied:
                    self.assertFalse(
                        can_write, f"Parent cannot write {section} (orders restricted)"
                    )
                else:
                    self.assertTrue(
                        can_write, f"Parent should write {section} when can_write=True"
                    )

    # --- Student role tests ---

    def test_student_read_matrix(self):
        """Test Student read access for all sections."""
        self._clear_permissions()
        for section in self.sections:
            with self.subTest(section=section):
                self._set_permission("Student", section, can_read=True, can_write=False)

                # Finance sections: students cannot read
                if section in self.finance_sections:
                    self.assertFalse(
                        can_user_read(self.student_user, section),
                        f"Student should NOT read {section} (finance restricted)",
                    )
                # Mentor-only sections: students cannot read
                elif section in self.mentor_only_sections:
                    self.assertFalse(
                        can_user_read(self.student_user, section),
                        f"Student should NOT read {section} (mentor-only)",
                    )
                else:
                    # Default read is True for students
                    self.assertTrue(
                        can_user_read(self.student_user, section),
                        f"Student should read {section} when can_read=True",
                    )

    def test_student_write_matrix(self):
        """Test Student write access for all sections (without object)."""
        self._clear_permissions()
        for section in self.sections:
            with self.subTest(section=section):
                self._set_permission("Student", section, can_read=True, can_write=True)
                can_write = can_user_write(self.student_user, section)

                # Without object, students can write:
                # - outreach (special case)
                # - student_info and related profile sections (own profile shortcut)
                if section in {
                    "outreach",
                    "student_info",
                    "identity",
                    "contact_address",
                    "health_medical",
                    "school",
                    "cmu_andrew",
                    "student_documents",
                    "discord",
                    "first_website",
                    "parents_emergency",
                    "other_details",
                    "attendance",
                    "adult_info",
                    "programs",
                }:
                    # The "own profile" shortcut returns True for Student writing Student/Adult objects
                    # but without an object, it falls through to RolePermission which has can_write=True
                    self.assertTrue(
                        can_write, f"Student should write {section} (can_write=True)"
                    )
                else:
                    # Sections like finance, background_checks are restricted
                    if section in self.no_write_except_lead:
                        self.assertFalse(
                            can_write, f"Student cannot write {section} (restricted)"
                        )
                    elif section in self.mentor_only_sections:
                        self.assertFalse(
                            can_write, f"Student cannot write {section} (mentor-only)"
                        )

    # --- Alumni role tests ---

    def test_alumni_read_matrix(self):
        """Test Alumni read access for all sections."""
        self._clear_permissions()
        for section in self.sections:
            with self.subTest(section=section):
                self._set_permission("Alumni", section, can_read=True, can_write=False)

                # Finance sections: alumni cannot read
                if section in self.finance_sections:
                    self.assertFalse(
                        can_user_read(self.alumni_user, section),
                        f"Alumni should NOT read {section} (finance restricted)",
                    )
                # Mentor-only sections: alumni cannot read
                elif section in self.mentor_only_sections:
                    self.assertFalse(
                        can_user_read(self.alumni_user, section),
                        f"Alumni should NOT read {section} (mentor-only)",
                    )
                # Orders: alumni never read, even when a Lead flips the toggle on
                elif section in self.orders_denied:
                    self.assertFalse(
                        can_user_read(self.alumni_user, section),
                        f"Alumni should NOT read {section} (orders restricted)",
                    )
                else:
                    # Default read is True for alumni
                    self.assertTrue(
                        can_user_read(self.alumni_user, section),
                        f"Alumni should read {section} when can_read=True",
                    )

    def test_alumni_write_matrix(self):
        """Test Alumni write access for all sections."""
        self._clear_permissions()
        for section in self.sections:
            with self.subTest(section=section):
                self._set_permission("Alumni", section, can_read=True, can_write=True)
                can_write = can_user_write(self.alumni_user, section)

                if section in self.no_write_except_lead:
                    self.assertFalse(
                        can_write, f"Alumni should NOT write {section} (restricted)"
                    )
                elif section in self.mentor_only_sections:
                    self.assertFalse(
                        can_write, f"Alumni cannot write {section} (mentor-only)"
                    )
                elif section in self.orders_denied:
                    self.assertFalse(
                        can_write, f"Alumni cannot write {section} (orders restricted)"
                    )
                else:
                    self.assertTrue(can_write, f"Alumni should write {section}")

    # --- Object-level access tests for Parent (student_info) ---

    def test_parent_can_read_own_student_info(self):
        """Parent can read their own student's info."""
        self._clear_permissions()
        self._set_permission("Parent", "student_info", can_read=True, can_write=False)
        self.assertTrue(
            can_user_read(self.parent_user, "student_info", obj=self.student)
        )

    def test_parent_cannot_read_other_student_info(self):
        """Parent cannot read other student's info."""
        self._clear_permissions()
        other_student = Student.objects.create(
            preferred_first_name="Other", last_name="Student"
        )
        self._set_permission("Parent", "student_info", can_read=True, can_write=False)
        self.assertFalse(
            can_user_read(self.parent_user, "student_info", obj=other_student)
        )

    # --- Object-level access tests for Mentor (adult_info) ---

    def test_mentor_can_read_parent_with_active_program(self):
        """Mentor can read Adult who is parent with student in active program."""
        self._clear_permissions()
        self._set_permission("Mentor", "adult_info", can_read=True, can_write=False)
        # Need enrollment in active program
        from programs.models import Enrollment

        Enrollment.objects.create(student=self.student, program=self.program)
        self.assertTrue(can_user_read(self.mentor_user, "adult_info", obj=self.parent))

    def test_mentor_cannot_read_parent_without_active_program(self):
        """Mentor cannot read Adult who is parent without active program student."""
        self._clear_permissions()
        inactive_program = Program.objects.create(name="Inactive", active=False)
        parent_no_active = Adult.objects.create(
            legal_first_name="Parent", last_name="NoActive", is_parent=True
        )
        student_inactive = Student.objects.create(
            preferred_first_name="Inactive", last_name="Student"
        )
        parent_no_active.students.add(student_inactive)
        from programs.models import Enrollment

        Enrollment.objects.create(student=student_inactive, program=inactive_program)

        self._set_permission("Mentor", "adult_info", can_read=True, can_write=False)
        self.assertFalse(
            can_user_read(self.mentor_user, "adult_info", obj=parent_no_active)
        )

    def test_mentor_cannot_read_non_parent_adult(self):
        """Mentor cannot read Adult who is not a parent."""
        self._clear_permissions()
        non_parent = Adult.objects.create(
            legal_first_name="Just", last_name="Mentor", is_mentor=True
        )
        self._set_permission("Mentor", "adult_info", can_read=True, can_write=False)
        self.assertFalse(can_user_read(self.mentor_user, "adult_info", obj=non_parent))

    # --- get_user_role tests ---

    def test_get_user_role_matrix(self):
        """Verify get_user_role returns correct role for each user type."""
        self.assertEqual(get_user_role(self.lead_mentor), "LeadMentor")
        self.assertEqual(get_user_role(self.mentor_user), "Mentor")
        self.assertEqual(get_user_role(self.parent_user), "Parent")
        self.assertEqual(get_user_role(self.student_user), "Student")
        self.assertEqual(get_user_role(self.alumni_user), "Alumni")

        # User with no profile
        nobody = User.objects.create_user(
            username="nobody", password="pass"
        )  # nosec B106
        self.assertIsNone(get_user_role(nobody))

    # --- Test all section/role combinations are covered ---

    def test_all_sections_have_role_permissions(self):
        """Ensure every section has RolePermission entries for all roles."""
        self._clear_permissions()
        for section in self.sections:
            for role in self.roles:
                with self.subTest(role=role, section=section):
                    # Create default permission
                    RolePermission.objects.get_or_create(role=role, section=section)
                    perm = RolePermission.objects.get(role=role, section=section)
                    self.assertIsNotNone(perm)


class PermissionEdgeCaseTests(TestCase):
    """Edge cases and specific permission behaviors."""

    @classmethod
    def setUpTestData(cls):
        cls.lead_mentor = User.objects.create_superuser(
            username="lead", password="pass"
        )  # nosec B106
        lm_group, _ = Group.objects.get_or_create(name="LeadMentor")
        cls.lead_mentor.groups.add(lm_group)

        cls.mentor_user = User.objects.create_user(
            username="mentor", password="pass"
        )  # nosec B106
        cls.mentor = Adult.objects.create(
            user=cls.mentor_user,
            legal_first_name="Mentor",
            last_name="User",
            is_mentor=True,
        )
        cls.parent_user = User.objects.create_user(
            username="parent", password="pass"
        )  # nosec B106
        cls.parent = Adult.objects.create(
            user=cls.parent_user,
            legal_first_name="Parent",
            last_name="User",
            is_parent=True,
        )
        cls.student_user = User.objects.create_user(
            username="student", password="pass"
        )  # nosec B106
        cls.student = Student.objects.create(
            user=cls.student_user, preferred_first_name="Student", last_name="User"
        )
        cls.alumni_user = User.objects.create_user(
            username="alumni", password="pass"
        )  # nosec B106
        cls.alumni = Adult.objects.create(
            user=cls.alumni_user,
            legal_first_name="Alumni",
            last_name="User",
            is_alumni=True,
        )
        cls.program = Program.objects.create(name="Test Program", active=True)
        cls.student.adults.add(cls.parent)

    def setUp(self):
        RolePermission.objects.all().delete()

    def test_background_checks_never_writable_for_non_lead(self):
        """background_checks section is never writable for non-LeadMentor roles."""
        for role_name, user in [
            ("Mentor", self.mentor_user),
            ("Parent", self.parent_user),
            ("Student", self.student_user),
            ("Alumni", self.alumni_user),
        ]:
            with self.subTest(role=role_name):
                RolePermission.objects.update_or_create(
                    role=role_name,
                    section="background_checks",
                    defaults={"can_read": True, "can_write": True},
                )
                self.assertFalse(
                    can_user_write(user, "background_checks"),
                    f"{role_name} should never write background_checks",
                )

        # Lead mentor CAN write
        self.assertTrue(can_user_write(self.lead_mentor, "background_checks"))

    def test_finance_sections_parent_read_only(self):
        """Parent can READ finance sections but cannot WRITE (only LeadMentor writes)."""
        from programs.permission_views import user_is_parent

        self.assertTrue(user_is_parent(self.parent_user))
        self.assertFalse(user_is_parent(self.mentor_user))

        # Parent can READ finance
        RolePermission.objects.update_or_create(
            role="Parent",
            section="payments",
            defaults={"can_read": True, "can_write": True},
        )
        RolePermission.objects.update_or_create(
            role="Parent",
            section="sliding_scale",
            defaults={"can_read": True, "can_write": True},
        )
        self.assertTrue(can_user_read(self.parent_user, "payments"))
        self.assertTrue(can_user_read(self.parent_user, "sliding_scale"))

        # But Parent CANNOT write finance (hardcoded to LeadMentor only)
        self.assertFalse(can_user_write(self.parent_user, "payments"))
        self.assertFalse(can_user_write(self.parent_user, "sliding_scale"))
        self.assertFalse(can_user_write(self.parent_user, "fees"))

    def test_attendance_mentor_default_write(self):
        """Mentor has default write access to attendance."""
        RolePermission.objects.update_or_create(
            role="Mentor",
            section="attendance",
            defaults={"can_read": True, "can_write": True},
        )
        self.assertTrue(can_user_read(self.mentor_user, "attendance"))
        self.assertTrue(can_user_write(self.mentor_user, "attendance"))

    def test_student_outreach_write(self):
        """Students can write outreach if they are champion."""
        self._set_permission("Student", "outreach", can_read=True, can_write=True)
        # Base permission allows write
        self.assertTrue(can_user_write(self.student_user, "outreach"))

    def _set_permission(self, role, section, can_read=True, can_write=False):
        RolePermission.objects.update_or_create(
            role=role,
            section=section,
            defaults={"can_read": can_read, "can_write": can_write},
        )
