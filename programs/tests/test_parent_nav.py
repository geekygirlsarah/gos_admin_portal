from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from programs.models import Adult, AdultStudentRelationship, RolePermission, Student


class ParentNavigationTests(TestCase):
    def setUp(self):
        # Create a parent user
        self.parent_user = User.objects.create_user(
            username="parent_user", password="password123"
        )  # nosec B106
        self.parent_adult = Adult.objects.create(
            user=self.parent_user,
            legal_first_name="Parent",
            last_name="One",
            is_parent=True,
        )

        # Create their child
        self.child = Student.objects.create(legal_first_name="Child", last_name="One")
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
        self.client.login(username="parent_user", password="password123")  # nosec B106
        response = self.client.get(reverse("profile_dashboard"))
        self.assertContains(response, 'href="/programs/students/"')
        self.assertContains(response, "Students")

    def test_parent_dashboard_has_view_profile_link(self):
        self.client.login(username="parent_user", password="password123")  # nosec B106
        response = self.client.get(reverse("profile_dashboard"))
        # Check if there's a link to the student's detail page
        detail_url = reverse("student_detail", args=[self.child.pk])
        self.assertContains(response, f'href="{detail_url}"')
        self.assertContains(response, "View Profile")
