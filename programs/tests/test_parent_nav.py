from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from programs.models import Adult, AdultStudentRelationship, RolePermission, Student


class ParentNavigationTests(TestCase):
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

        # Ensure RolePermission allows Parents to see student_info
        RolePermission.objects.update_or_create(
            role="Parent",
            section="student_info",
            defaults={"can_read": True, "can_write": True},
        )

    def test_parent_navbar_has_students_link(self):
        self.client.login(username="parent_user", password="password123")
        response = self.client.get(reverse("profile_dashboard"))
        self.assertContains(response, 'href="/programs/students/"')
        self.assertContains(response, "Students")

    def test_parent_dashboard_has_view_profile_link(self):
        self.client.login(username="parent_user", password="password123")
        response = self.client.get(reverse("profile_dashboard"))
        # Check if there's a link to the student's detail page
        detail_url = reverse("student_detail", args=[self.child.pk])
        self.assertContains(response, f'href="{detail_url}"')
        self.assertContains(response, "View Profile")

    # def test_parent_dashboard_has_edit_profile_link_when_permitted(self):
    #     self.client.login(username="parent_user", password="password123")
    #     response = self.client.get(reverse("profile_dashboard"))
    #     edit_url = reverse("student_edit", args=[self.child.pk])
    #     self.assertContains(response, f'href="{edit_url}"')
    #     self.assertContains(response, "Edit Profile")

    # def test_parent_dashboard_still_has_edit_profile_link_even_if_global_can_write_is_false(self):
    #     # Even if global write permission is removed, parents can still edit their own children
    #     RolePermission.objects.filter(role="Parent", section="student_info").update(can_write=False)
    #
    #     self.client.login(username="parent_user", password="password123")
    #     response = self.client.get(reverse("profile_dashboard"))
    #     edit_url = reverse("student_edit", args=[self.child.pk])
    #     self.assertContains(response, f'href="{edit_url}"')
    #     self.assertContains(response, "Edit Profile")
