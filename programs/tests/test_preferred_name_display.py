from django.contrib.auth.models import Group, User
from django.test import Client, TestCase
from django.urls import reverse

from programs.models import Adult, Student


class PreferredNameDisplayTest(TestCase):
    """Preferred names should be shown instead of legal names on list/detail pages."""

    def setUp(self):
        self.user = User.objects.create_superuser(
            username="leadmentor", email="lead@example.com", password="password"  # nosec B106
        )
        group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.user.groups.add(group)

        self.client = Client()
        self.client.login(username="leadmentor", password="password")  # nosec B106

        self.mentor = Adult.objects.create(
            first_name="Robert",
            preferred_first_name="Bobby",
            last_name="Smith",
            is_mentor=True,
            active=True,
            personal_email="bobby@example.com",
        )
        self.parent = Adult.objects.create(
            first_name="James",
            preferred_first_name="Jim",
            last_name="Jones",
            is_parent=True,
            active=True,
            personal_email="jim@example.com",
            email_updates=True,
        )
        self.alumni = Adult.objects.create(
            first_name="Patricia",
            preferred_first_name="Pat",
            last_name="Lee",
            is_alumni=True,
            active=True,
            personal_email="pat@example.com",
        )
        self.legal_only = Adult.objects.create(
            first_name="Michael",
            last_name="Brown",
            is_mentor=True,
            is_parent=True,
            active=True,
            personal_email="michael@example.com",
            email_updates=True,
        )

        self.student = Student.objects.create(first_name="Test", last_name="Student")
        self.parent.students.add(self.student)
        self.legal_only.students.add(self.student)

    def test_mentor_list_shows_preferred_name(self):
        response = self.client.get(reverse("mentor_list"))
        self.assertContains(response, "Bobby Smith")
        self.assertNotContains(response, "Robert Smith")

    def test_mentor_list_shows_legal_name_when_no_preferred(self):
        response = self.client.get(reverse("mentor_list"))
        self.assertContains(response, "Michael Brown")

    def test_adult_list_shows_preferred_name(self):
        response = self.client.get(reverse("adult_list"))
        self.assertContains(response, "Bobby Smith")
        self.assertNotContains(response, "Robert Smith")

    def test_parent_list_shows_preferred_name(self):
        response = self.client.get(reverse("parent_list"))
        self.assertContains(response, "Jim Jones")
        self.assertNotContains(response, "James Jones")

    def test_alumni_list_shows_preferred_name(self):
        response = self.client.get(reverse("alumni_list"))
        self.assertContains(response, "Pat Lee")
        self.assertNotContains(response, "Patricia Lee")

    def test_adult_detail_heading_shows_preferred_name(self):
        response = self.client.get(reverse("adult_detail", args=[self.mentor.pk]))
        self.assertContains(response, "Bobby Smith")
        self.assertNotContains(response, "Robert Smith")

    def test_adult_detail_still_shows_legal_name_field(self):
        """The detail fields section should still show legal first name."""
        response = self.client.get(reverse("adult_detail", args=[self.mentor.pk]))
        self.assertContains(response, "Robert")
        self.assertContains(response, "Bobby")
