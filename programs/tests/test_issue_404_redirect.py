from django.contrib.auth.models import Permission, User
from django.test import TestCase
from django.urls import reverse

from programs.models import Adult, RolePermission, Student


class Redirect404Tests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser", password="password123"
        )  # nosec B106
        # Give them Parent role so they have general permission to view student_info section
        RolePermission.objects.update_or_create(
            role="Parent",
            section="student_info",
            defaults={"can_read": True, "can_write": True},
        )
        # Give them Parent role (via profile) so they have general permission to view adult_info
        RolePermission.objects.update_or_create(
            role="Parent",
            section="adult_info",
            defaults={"can_read": True, "can_write": True},
        )

        # Link user to an Adult profile with Parent role
        self.adult = Adult.objects.create(
            user=self.user, legal_first_name="Test", last_name="Parent", is_parent=True
        )

    def test_non_existent_student_redirects_to_home(self):
        self.client.login(username="testuser", password="password123")  # nosec B106
        # Student with ID 9999 does not exist
        url = reverse("student_detail", args=[9999])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("home"))

        # Check message
        messages = list(response.wsgi_request._messages)
        self.assertEqual(len(messages), 1)
        self.assertEqual(
            str(messages[0]),
            "You do not have permission to view that student, or it does not exist.",
        )

    def test_non_existent_adult_redirects_to_home(self):
        self.client.login(username="testuser", password="password123")  # nosec B106
        # Adult with ID 9999 does not exist
        url = reverse("adult_detail", args=[9999])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("home"))

        # Check message
        messages = list(response.wsgi_request._messages)
        self.assertEqual(len(messages), 1)
        self.assertEqual(
            str(messages[0]),
            "You do not have permission to view that adult, or it does not exist.",
        )

    def test_unauthorized_student_redirects(self):
        other_student = Student.objects.create(
            preferred_first_name="Other", last_name="Student"
        )
        self.client.login(username="testuser", password="password123")  # nosec B106
        url = reverse("student_detail", args=[other_student.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("home"))

        # Check message
        messages = list(response.wsgi_request._messages)
        self.assertEqual(len(messages), 1)
        self.assertEqual(
            str(messages[0]),
            "You do not have permission to view that student, or it does not exist.",
        )

    def test_non_existent_student_edit_redirects_to_home(self):
        self.client.login(username="testuser", password="password123")  # nosec B106
        # Student with ID 9999 does not exist
        url = reverse("student_edit", args=[9999])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("home"))

        # Check message
        messages = list(response.wsgi_request._messages)
        self.assertEqual(len(messages), 1)
        self.assertEqual(
            str(messages[0]),
            "You do not have permission to view that student, or it does not exist.",
        )
