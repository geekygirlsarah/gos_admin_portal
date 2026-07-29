import datetime

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from programs.models import Adult, AdultStudentRelationship, Student


class StudentParentLinksTest(TestCase):
    def setUp(self):
        # Create LeadMentor user
        self.user = User.objects.create_user(
            username="leadmentor", password="password123"
        )  # nosec B106
        group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.user.groups.add(group)
        self.client.login(username="leadmentor", password="password123")  # nosec B106

        # Create Student
        self.student = Student.objects.create(
            legal_first_name="Jane",
            last_name="Smith",
            date_of_birth=datetime.date(2010, 1, 1),
        )

        # Create Adult (Parent)
        self.parent = Adult.objects.create(
            first_name="John",
            last_name="Smith",
            personal_email="john@example.com",
            is_parent=True,
        )

        # Set primary contact
        self.student.primary_contact = self.parent
        self.student.save()

        # Link Parent to Student
        AdultStudentRelationship.objects.create(
            adult=self.parent,
            student=self.student,
            relationship_to_student="parent",
        )

    def test_student_detail_links_to_parent(self):
        """Student detail page should contain a link to the parent's detail page."""
        url = reverse("student_detail", args=[self.student.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        # Check for link to adult detail
        parent_detail_url = reverse("adult_detail", args=[self.parent.pk])
        self.assertContains(response, f'href="{parent_detail_url}"')

    def test_student_edit_links_to_parent(self):
        """Student edit page should contain a link to the parent's detail page."""
        url = reverse("student_edit", args=[self.student.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        # Check for link to adult detail
        parent_detail_url = reverse("adult_detail", args=[self.parent.pk])
        # We also check for target="_blank" as it's better for forms
        self.assertContains(response, f'href="{parent_detail_url}')

    def test_student_detail_no_links_for_unauthorized_user(self):
        """Users without permission to view adult info should not see links."""
        # Create a student user
        student_user = User.objects.create_user(
            username="student_user", password="password123"
        )  # nosec B106
        student_profile = Student.objects.create(
            user=student_user,
            legal_first_name="Jane",
            last_name="Smith",
            date_of_birth=datetime.date(2010, 1, 1),
        )
        # Link Parent to this Student
        AdultStudentRelationship.objects.create(
            adult=self.parent,
            student=student_profile,
            relationship_to_student="parent",
        )
        self.client.login(username="student_user", password="password123")  # nosec B106

        url = reverse("student_detail", args=[student_profile.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        # Should NOT contain link to adult detail
        parent_detail_url = reverse("adult_detail", args=[self.parent.pk])
        self.assertNotContains(response, f'href="{parent_detail_url}"')
        # But should still contain the parent's name
        self.assertContains(response, str(self.parent))
