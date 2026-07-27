from django.contrib.auth.models import Group, User
from django.test import TestCase

from programs.models import Adult, Student
from programs.permission_views import get_user_role


class GetUserRoleTests(TestCase):
    def test_superuser_is_lead_mentor(self):
        user = User.objects.create_superuser(
            username="admin", password="password123"
        )  # nosec B106
        self.assertEqual(get_user_role(user), "LeadMentor")

    def test_lead_mentor_group_is_lead_mentor(self):
        user = User.objects.create_user(
            username="lm", password="password123"
        )  # nosec B106
        lm_group, _ = Group.objects.get_or_create(name="LeadMentor")
        user.groups.add(lm_group)
        self.assertEqual(get_user_role(user), "LeadMentor")

    def test_mentor_priority_over_parent_and_alumni(self):
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
        user = User.objects.create_user(
            username="stu", password="password123"
        )  # nosec B106
        Student.objects.create(user=user, first_name="Student", last_name="User")
        self.assertEqual(get_user_role(user), "Student")

    def test_lead_mentor_group_overrides_profiles(self):
        user = User.objects.create_user(
            username="lm_with_profile", password="password123"
        )  # nosec B106
        # profile says Mentor
        Adult.objects.create(
            user=user, first_name="Test", last_name="User", is_mentor=True
        )
        # group says LeadMentor
        lm_group, _ = Group.objects.get_or_create(name="LeadMentor")
        user.groups.add(lm_group)
        self.assertEqual(get_user_role(user), "LeadMentor")

    def test_no_profile_or_group_returns_none(self):
        user = User.objects.create_user(
            username="nobody", password="password123"
        )  # nosec B106
        self.assertIsNone(get_user_role(user))

    def test_group_fallback_mentor(self):
        user = User.objects.create_user(
            username="mentor_group", password="password123"
        )  # nosec B106
        group, _ = Group.objects.get_or_create(name="Mentor")
        user.groups.add(group)
        self.assertEqual(get_user_role(user), "Mentor")

    def test_group_fallback_parent(self):
        user = User.objects.create_user(
            username="parent_group", password="password123"
        )  # nosec B106
        group, _ = Group.objects.get_or_create(name="Parent")
        user.groups.add(group)
        self.assertEqual(get_user_role(user), "Parent")

    def test_group_fallback_student(self):
        user = User.objects.create_user(
            username="student_group", password="password123"
        )  # nosec B106
        group, _ = Group.objects.get_or_create(name="Student")
        user.groups.add(group)
        self.assertEqual(get_user_role(user), "Student")
