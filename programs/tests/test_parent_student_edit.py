from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from programs.models import Adult, AdultStudentRelationship, RolePermission, Student


class ParentStudentEditTests(TestCase):
    def setUp(self):
        # Create a parent user
        self.parent_user = User.objects.create_user(
            username="parent_user", password="password123"
        )
        self.parent_adult = Adult.objects.create(
            user=self.parent_user, first_name="Parent", last_name="One", is_parent=True
        )

        # Create their child
        self.child = Student.objects.create(first_name="Child", last_name="One")
        AdultStudentRelationship.objects.create(
            adult=self.parent_adult,
            student=self.child,
            relationship_to_student="parent",
        )

        # Create another student (not their child)
        self.other_student = Student.objects.create(
            first_name="Other", last_name="Student"
        )

        # Ensure RolePermission allows Parents to see student_info (but maybe not write yet)
        RolePermission.objects.update_or_create(
            role="Parent",
            section="student_info",
            defaults={"can_read": True, "can_write": False},
        )

    def test_parent_can_view_child_detail(self):
        self.client.login(username="parent_user", password="password123")
        url = reverse("student_detail", args=[self.child.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_parent_cannot_view_other_student_detail(self):
        self.client.login(username="parent_user", password="password123")
        url = reverse("student_detail", args=[self.other_student.pk])
        response = self.client.get(url)
        # Should redirect to home because they don't have permission for other students
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("home"))

    def test_parent_can_edit_child_student_even_if_can_write_is_false(self):
        self.client.login(username="parent_user", password="password123")
        url = reverse("student_edit", args=[self.child.pk])
        response = self.client.get(url)
        # Should now be 200 because it's their child (Always allow override)
        self.assertEqual(response.status_code, 200)

    def test_parent_can_edit_child_student_if_can_write_is_true(self):
        # Update permission to allow writing
        RolePermission.objects.filter(role="Parent", section="student_info").update(
            can_write=True
        )

        self.client.login(username="parent_user", password="password123")
        url = reverse("student_edit", args=[self.child.pk])
        response = self.client.get(url)
        # Should be 200 now
        self.assertEqual(response.status_code, 200)

    def test_parent_cannot_edit_other_student_even_if_can_write_is_true(self):
        # Update permission to allow writing
        RolePermission.objects.filter(role="Parent", section="student_info").update(
            can_write=True
        )

        self.client.login(username="parent_user", password="password123")
        url = reverse("student_edit", args=[self.other_student.pk])
        response = self.client.get(url)
        # Should still redirect to home because of object-level restriction
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("home"))
