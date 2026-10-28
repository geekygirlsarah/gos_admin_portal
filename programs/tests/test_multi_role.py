"""Comprehensive test suite for multi-role user scenarios and role permission interactions.

Covers dual and triple role configurations (Parent+Mentor, Parent+Alumni, Mentor+Alumni,
Parent+Mentor+Alumni, LeadMentor+Adult), role resolution priority, flag helpers,
view gating, object-level scoping, and active/inactive mentor transitions.
"""

from django.contrib.auth.models import Group, User
from django.test import Client, TestCase
from django.urls import reverse

from programs.models import (
    Adult,
    AdultStudentRelationship,
    Enrollment,
    Fee,
    Payment,
    Program,
    RolePermission,
    SlidingScale,
    Student,
)
from programs.permission_views import (
    can_user_read,
    can_user_write,
    get_user_role,
    user_is_alumni,
    user_is_mentor,
    user_is_mentor_or_lead,
    user_is_parent,
)


class MultiRoleResolutionAndHelpersTests(TestCase):
    """Test get_user_role priority and individual user_is_* flag helper functions."""

    def setUp(self):
        self.lead_group, _ = Group.objects.get_or_create(name="LeadMentor")

    def test_single_role_resolutions(self):
        # LeadMentor superuser
        u_lead = User.objects.create_superuser(
            username="u_lead", password="password123"  # nosec B106
        )
        self.assertEqual(get_user_role(u_lead), "LeadMentor")
        self.assertTrue(user_is_mentor_or_lead(u_lead))
        self.assertFalse(user_is_parent(u_lead))
        self.assertFalse(user_is_mentor(u_lead))
        self.assertFalse(user_is_alumni(u_lead))

        # Active Mentor
        u_mentor = User.objects.create_user(
            username="u_mentor", password="password123"  # nosec B106
        )
        Adult.objects.create(
            user=u_mentor,
            legal_first_name="Active",
            last_name="Mentor",
            is_mentor=True,
            mentor_active=True,
        )
        self.assertEqual(get_user_role(u_mentor), "Mentor")
        self.assertTrue(user_is_mentor(u_mentor))
        self.assertTrue(user_is_mentor_or_lead(u_mentor))
        self.assertFalse(user_is_parent(u_mentor))
        self.assertFalse(user_is_alumni(u_mentor))

        # Parent
        u_parent = User.objects.create_user(
            username="u_parent", password="password123"  # nosec B106
        )
        Adult.objects.create(
            user=u_parent,
            legal_first_name="Sole",
            last_name="Parent",
            is_parent=True,
        )
        self.assertEqual(get_user_role(u_parent), "Parent")
        self.assertTrue(user_is_parent(u_parent))
        self.assertFalse(user_is_mentor(u_parent))
        self.assertFalse(user_is_alumni(u_parent))
        self.assertFalse(user_is_mentor_or_lead(u_parent))

        # Alumni
        u_alumni = User.objects.create_user(
            username="u_alumni", password="password123"  # nosec B106
        )
        Adult.objects.create(
            user=u_alumni,
            legal_first_name="Sole",
            last_name="Alumni",
            is_alumni=True,
        )
        self.assertEqual(get_user_role(u_alumni), "Alumni")
        self.assertTrue(user_is_alumni(u_alumni))
        self.assertFalse(user_is_parent(u_alumni))
        self.assertFalse(user_is_mentor(u_alumni))

        # Student
        u_student = User.objects.create_user(
            username="u_student", password="password123"  # nosec B106
        )
        Student.objects.create(
            user=u_student,
            legal_first_name="Sole",
            last_name="Student",
        )
        self.assertEqual(get_user_role(u_student), "Student")
        self.assertFalse(user_is_parent(u_student))
        self.assertFalse(user_is_mentor(u_student))

    def test_parent_and_active_mentor_dual_role(self):
        """When an adult is both a Parent and an active Mentor, get_user_role returns
        'Mentor' (priority order), but user_is_parent and user_is_mentor are both True.
        """
        user = User.objects.create_user(
            username="parent_mentor", password="password123"  # nosec B106
        )
        adult = Adult.objects.create(
            user=user,
            legal_first_name="Parent",
            last_name="Mentor",
            is_parent=True,
            is_mentor=True,
            mentor_active=True,
        )
        self.assertEqual(get_user_role(user), "Mentor")
        self.assertTrue(user_is_parent(user))
        self.assertTrue(user_is_mentor(user))
        self.assertTrue(user_is_mentor_or_lead(user))
        self.assertFalse(user_is_alumni(user))

        # When deactivated as a mentor, get_user_role falls back to Parent
        adult.mentor_active = False
        adult.save()
        self.assertEqual(get_user_role(user), "Parent")
        self.assertTrue(user_is_parent(user))
        self.assertFalse(user_is_mentor(user))
        self.assertFalse(user_is_mentor_or_lead(user))

    def test_parent_and_alumni_dual_role(self):
        """When an adult is both a Parent and Alumni, get_user_role returns 'Parent'."""
        user = User.objects.create_user(
            username="parent_alumni", password="password123"  # nosec B106
        )
        Adult.objects.create(
            user=user,
            legal_first_name="Parent",
            last_name="Alumni",
            is_parent=True,
            is_alumni=True,
        )
        self.assertEqual(get_user_role(user), "Parent")
        self.assertTrue(user_is_parent(user))
        self.assertTrue(user_is_alumni(user))
        self.assertFalse(user_is_mentor(user))
        self.assertFalse(user_is_mentor_or_lead(user))

    def test_mentor_and_alumni_dual_role(self):
        """When an adult is Mentor and Alumni, Mentor takes priority if active."""
        user = User.objects.create_user(
            username="mentor_alumni", password="password123"  # nosec B106
        )
        adult = Adult.objects.create(
            user=user,
            legal_first_name="Mentor",
            last_name="Alumni",
            is_mentor=True,
            mentor_active=True,
            is_alumni=True,
        )
        self.assertEqual(get_user_role(user), "Mentor")
        self.assertTrue(user_is_mentor(user))
        self.assertTrue(user_is_alumni(user))
        self.assertFalse(user_is_parent(user))

        # Deactivating mentor status falls back to Alumni
        adult.mentor_active = False
        adult.save()
        self.assertEqual(get_user_role(user), "Alumni")
        self.assertFalse(user_is_mentor(user))
        self.assertTrue(user_is_alumni(user))

    def test_triple_role_parent_mentor_alumni(self):
        """An adult with all three roles."""
        user = User.objects.create_user(
            username="triple_role", password="password123"  # nosec B106
        )
        adult = Adult.objects.create(
            user=user,
            legal_first_name="All",
            last_name="Roles",
            is_parent=True,
            is_mentor=True,
            mentor_active=True,
            is_alumni=True,
        )
        # Priority: Mentor -> Parent -> Alumni
        self.assertEqual(get_user_role(user), "Mentor")
        self.assertTrue(user_is_parent(user))
        self.assertTrue(user_is_mentor(user))
        self.assertTrue(user_is_alumni(user))

        # Deactivate mentor -> becomes Parent
        adult.mentor_active = False
        adult.save()
        self.assertEqual(get_user_role(user), "Parent")
        self.assertTrue(user_is_parent(user))
        self.assertFalse(user_is_mentor(user))
        self.assertTrue(user_is_alumni(user))

    def test_lead_mentor_group_with_adult_profile(self):
        """A user in the LeadMentor group with an adult profile resolves to LeadMentor."""
        user = User.objects.create_user(
            username="lead_adult", password="password123"  # nosec B106
        )
        user.groups.add(self.lead_group)
        Adult.objects.create(
            user=user,
            legal_first_name="Lead",
            last_name="Parent",
            is_parent=True,
            is_mentor=True,
        )
        self.assertEqual(get_user_role(user), "LeadMentor")
        self.assertTrue(user_is_mentor_or_lead(user))
        self.assertTrue(user_is_parent(user))
        self.assertTrue(user_is_mentor(user))

    def test_anonymous_and_none_user(self):
        self.assertIsNone(get_user_role(None))
        self.assertFalse(user_is_parent(None))
        self.assertFalse(user_is_mentor(None))
        self.assertFalse(user_is_alumni(None))
        self.assertFalse(user_is_mentor_or_lead(None))
        self.assertFalse(can_user_read(None, "programs"))
        self.assertFalse(can_user_write(None, "programs"))


class MultiRolePermissionAccessTests(TestCase):
    """Test object-level permissions and view access for multi-role users."""

    def setUp(self):
        self.client = Client()

        # Program
        self.program = Program.objects.create(name="FRC Robotics 2026", active=True)

        # Student 1 (child of parent/mentor)
        self.student1 = Student.objects.create(
            legal_first_name="Emma",
            last_name="Watson",
        )
        self.enrollment1 = Enrollment.objects.create(
            student=self.student1, program=self.program, active=True
        )

        # Student 2 (unrelated)
        self.student2 = Student.objects.create(
            legal_first_name="Lucas",
            last_name="Granger",
        )
        self.enrollment2 = Enrollment.objects.create(
            student=self.student2, program=self.program, active=True
        )

        # Dual Role User: Parent of Student 1 + Active Mentor
        self.parent_mentor_user = User.objects.create_user(
            username="parent_mentor_user", password="password123"  # nosec B106
        )
        self.parent_mentor = Adult.objects.create(
            user=self.parent_mentor_user,
            legal_first_name="Sarah",
            last_name="Watson",
            is_parent=True,
            is_mentor=True,
            mentor_active=True,
        )
        AdultStudentRelationship.objects.create(
            adult=self.parent_mentor,
            student=self.student1,
            relationship_to_student="parent",
        )

        # Fees and Sliding Scale
        self.fee = Fee.objects.create(
            program=self.program,
            name="Season Registration",
            amount=100,
        )
        self.sliding_scale = SlidingScale.objects.create(
            student=self.student1,
            percent=50,
            status=SlidingScale.STATUS_APPROVED,
        )
        self.payment = Payment.objects.create(
            student=self.student1,
            program=self.program,
            amount=50,
            paid_on="2026-01-15",
            paid_via="check",
        )

        # Permissions
        RolePermission.objects.update_or_create(
            role="Mentor",
            section="attendance",
            defaults={"can_read": True, "can_write": True},
        )
        RolePermission.objects.update_or_create(
            role="Parent",
            section="payments",
            defaults={"can_read": True, "can_write": False},
        )
        RolePermission.objects.update_or_create(
            role="Parent",
            section="sliding_scale",
            defaults={"can_read": True, "can_write": False},
        )
        RolePermission.objects.update_or_create(
            role="Parent",
            section="fees",
            defaults={"can_read": True, "can_write": False},
        )

    def test_parent_mentor_finance_object_level_isolation(self):
        """Parent+Mentor can read finance objects for their child, but NOT for other students."""
        user = self.parent_mentor_user

        # Child 1: Allowed
        self.assertTrue(can_user_read(user, "payments", self.student1))
        self.assertTrue(can_user_read(user, "payments", self.payment))
        self.assertTrue(can_user_read(user, "sliding_scale", self.sliding_scale))
        self.assertTrue(can_user_read(user, "fees", self.fee))

        # Child 2 (unrelated): Denied
        self.assertFalse(can_user_read(user, "payments", self.student2))
        payment2 = Payment.objects.create(
            student=self.student2,
            program=self.program,
            amount=100,
            paid_on="2026-01-15",
        )
        sliding2 = SlidingScale.objects.create(
            student=self.student2,
            percent=25,
            status=SlidingScale.STATUS_APPROVED,
        )
        self.assertFalse(can_user_read(user, "payments", payment2))
        self.assertFalse(can_user_read(user, "sliding_scale", sliding2))

    def test_parent_mentor_view_integration(self):
        """HTTP integration checks for dual-role parent/mentor."""
        self.client.login(
            username="parent_mentor_user", password="password123"  # nosec B106
        )

        # Can view own child's balance sheet
        url_child = reverse(
            "program_student_balance", args=[self.program.pk, self.student1.pk]
        )
        resp_child = self.client.get(url_child)
        self.assertEqual(resp_child.status_code, 200)

        # Cannot view other student's balance sheet
        url_other = reverse(
            "program_student_balance", args=[self.program.pk, self.student2.pk]
        )
        resp_other = self.client.get(url_other)
        self.assertEqual(resp_other.status_code, 302)

        # Can access Payments summary page as a parent
        resp_payments = self.client.get(reverse("parent_payments"))
        self.assertEqual(resp_payments.status_code, 200)

        # Can access Program Email view as an active mentor
        resp_email = self.client.get(reverse("program_email", args=[self.program.pk]))
        self.assertEqual(resp_email.status_code, 200)

    def test_inactive_mentor_loses_mentor_views_but_keeps_parent_views(self):
        """When mentor_active is False, user loses program email/staff access but keeps parent payments."""
        self.parent_mentor.mentor_active = False
        self.parent_mentor.save()

        self.client.login(
            username="parent_mentor_user", password="password123"  # nosec B106
        )

        # Parent balance access still works
        url_child = reverse(
            "program_student_balance", args=[self.program.pk, self.student1.pk]
        )
        resp_child = self.client.get(url_child)
        self.assertEqual(resp_child.status_code, 200)

        # Staff mentor email view now redirects/denies
        resp_email = self.client.get(reverse("program_email", args=[self.program.pk]))
        self.assertEqual(resp_email.status_code, 302)
