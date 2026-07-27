from django.contrib.auth.models import Permission, User
from django.test import TestCase
from django.urls import reverse

from programs.models import Adult, AdultStudentRelationship, RolePermission, Student


class ProfilePermissionsTests(TestCase):
    def setUp(self):
        # Create a Student
        self.student_user = User.objects.create_user(
            username="student_user", password="password123", email="student@example.com"
        )
        self.student = Student.objects.create(
            user=self.student_user,
            first_name="Student",
            last_name="One",
            personal_email="student@example.com",
        )

        # Create another Student
        self.other_student_user = User.objects.create_user(
            username="other_student", password="password123"
        )
        self.other_student = Student.objects.create(
            user=self.other_student_user, first_name="Other", last_name="Student"
        )

        # Create a Parent
        self.parent_user = User.objects.create_user(
            username="parent_user", password="password123"
        )
        self.parent = Adult.objects.create(
            user=self.parent_user, first_name="Parent", last_name="One", is_parent=True
        )
        # Link Parent to Student One
        AdultStudentRelationship.objects.create(
            adult=self.parent, student=self.student, relationship_to_student="parent"
        )

        # Create another Parent
        self.other_parent_user = User.objects.create_user(
            username="other_parent", password="password123"
        )
        self.other_parent = Adult.objects.create(
            user=self.other_parent_user,
            first_name="Other",
            last_name="Parent",
            is_parent=True,
        )

        # Create a Mentor
        self.mentor_user = User.objects.create_user(
            username="mentor_user", password="password123"
        )
        self.mentor = Adult.objects.create(
            user=self.mentor_user, first_name="Mentor", last_name="One", is_mentor=True
        )

        # Create an Alumni
        self.alumni_user = User.objects.create_user(
            username="alumni_user", password="password123"
        )
        self.alumni = Adult.objects.create(
            user=self.alumni_user,
            first_name="Alumni",
            last_name="One",
            is_alumni=True,
            student_record=self.other_student,  # Alumni of 'other_student' record
        )

        # Create a Lead Mentor
        self.lead_mentor_user = User.objects.create_superuser(
            username="lead_mentor", password="password123"
        )

        # Ensure RolePermission exists
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

        # Give necessary Django permissions for the views that require them
        self.change_student_perm = Permission.objects.get(codename="change_student")
        self.change_adult_perm = Permission.objects.get(codename="change_adult")

    def test_student_can_view_own_profile(self):
        self.client.login(username="student_user", password="password123")
        url = reverse("student_detail", args=[self.student.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Student One")

    def test_student_cannot_view_other_student_profile(self):
        self.client.login(username="student_user", password="password123")
        url = reverse("student_detail", args=[self.other_student.pk])
        response = self.client.get(url)
        # Redirects to home/dashboard on failure
        self.assertEqual(response.status_code, 302)

    def test_student_can_edit_own_profile(self):
        self.student_user.user_permissions.add(self.change_student_perm)
        self.client.login(username="student_user", password="password123")
        url = reverse("student_edit", args=[self.student.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_student_cannot_edit_other_student_profile(self):
        self.student_user.user_permissions.add(self.change_student_perm)
        self.client.login(username="student_user", password="password123")
        url = reverse("student_edit", args=[self.other_student.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

    def test_parent_can_view_own_profile(self):
        self.client.login(username="parent_user", password="password123")
        url = reverse("adult_detail", args=[self.parent.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_parent_cannot_view_other_adult_profile(self):
        self.client.login(username="parent_user", password="password123")
        url = reverse("adult_detail", args=[self.other_parent.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

    def test_parent_can_edit_own_profile(self):
        self.parent_user.user_permissions.add(self.change_adult_perm)
        self.client.login(username="parent_user", password="password123")
        url = reverse("parent_edit", args=[self.parent.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_parent_cannot_edit_other_adult_profile(self):
        self.parent_user.user_permissions.add(self.change_adult_perm)
        self.client.login(username="parent_user", password="password123")
        url = reverse("parent_edit", args=[self.other_parent.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

    def test_parent_can_view_linked_student(self):
        self.client.login(username="parent_user", password="password123")
        url = reverse("student_detail", args=[self.student.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_parent_cannot_view_unlinked_student(self):
        self.client.login(username="parent_user", password="password123")
        url = reverse("student_detail", args=[self.other_student.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

    def test_mentor_can_view_own_profile(self):
        self.client.login(username="mentor_user", password="password123")
        url = reverse("adult_detail", args=[self.mentor.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_mentor_cannot_view_other_adult_profile(self):
        # Mentors are restricted to Parents who have a student enrolled in an
        # active program. ``other_parent`` has no students at all, so the
        # Mentor must not be able to view this profile.
        self.client.login(username="mentor_user", password="password123")
        url = reverse("adult_detail", args=[self.other_parent.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

    def test_alumni_can_view_own_profile(self):
        self.client.login(username="alumni_user", password="password123")
        url = reverse("adult_detail", args=[self.alumni.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_alumni_cannot_view_other_adult_profile(self):
        self.client.login(username="alumni_user", password="password123")
        url = reverse("adult_detail", args=[self.parent.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

    def test_alumni_can_view_own_student_record(self):
        self.client.login(username="alumni_user", password="password123")
        url = reverse("student_detail", args=[self.other_student.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_lead_mentor_can_view_any_profile(self):
        self.client.login(username="lead_mentor", password="password123")

        # Can view any student
        url = reverse("student_detail", args=[self.student.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        # Can view any adult
        url = reverse("adult_detail", args=[self.parent.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_student_list_is_filtered_for_parent(self):
        self.client.login(username="parent_user", password="password123")
        url = reverse("student_list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Student One")
        self.assertNotContains(response, "Other Student")

    def test_student_list_is_filtered_for_student(self):
        self.client.login(username="student_user", password="password123")
        url = reverse("student_list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Student One")
        self.assertNotContains(response, "Other Student")

    def test_emergency_contacts_is_filtered_for_parent(self):
        self.client.login(username="parent_user", password="password123")
        url = reverse("student_emergency_contacts")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Student One")
        self.assertNotContains(response, "Other Student")
