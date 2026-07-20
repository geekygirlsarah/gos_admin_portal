from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from applications.models import Application
from programs.models import Adult, Program, School, Student


class ListSortingTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username="admin",
            password="password",
            email="admin@example.com",  # nosec B106
        )
        self.client = Client()
        self.client.force_login(self.user)

        # Give user application review permission
        from django.contrib.auth.models import Permission

        perm = Permission.objects.get(codename="review_application")
        self.user.user_permissions.add(perm)

        # Create students for sorting test
        school_a = School.objects.create(name="High School A")
        school_b = School.objects.create(name="High School B")
        school_c = School.objects.create(name="High School C")

        Student.objects.create(
            legal_first_name="Alice",
            last_name="Zebra",
            school=school_a,
            graduation_year=2025,
        )
        Student.objects.create(
            legal_first_name="Charlie",
            last_name="Xray",
            school=school_b,
            graduation_year=2024,
        )
        Student.objects.create(
            legal_first_name="Bob",
            last_name="Yankee",
            school=school_c,
            graduation_year=2026,
        )

        # Create adults for sorting test
        Adult.objects.create(
            first_name="John", last_name="Doe", personal_email="john@example.com"
        )
        Adult.objects.create(
            first_name="Jane", last_name="Smith", personal_email="jane@example.com"
        )
        Adult.objects.create(
            first_name="Alice", last_name="Brown", personal_email="alice@example.com"
        )

        # Create applications for sorting test
        Application.objects.create(
            application_id="APP1", applicant_type="STUDENT", email="c@example.com"
        )
        Application.objects.create(
            application_id="APP2", applicant_type="MENTOR", email="a@example.com"
        )
        Application.objects.create(
            application_id="APP3", applicant_type="STUDENT", email="b@example.com"
        )

    def test_student_list_sorting_by_name(self):
        # Default sorting is by first_name, last_name
        response = self.client.get(reverse("student_list"))
        students = list(response.context["students"])
        self.assertEqual(students[0].legal_first_name, "Alice")
        self.assertEqual(students[1].legal_first_name, "Bob")
        self.assertEqual(students[2].legal_first_name, "Charlie")

        # Sort by name asc (default for students is by first name then last name)
        # Alice Zebra, Bob Yankee, Charlie Xray -> Alice Zebra, Bob Yankee, Charlie Xray
        response = self.client.get(reverse("student_list") + "?sort=name&dir=asc")
        students = list(response.context["students"])
        self.assertEqual(students[0].legal_first_name, "Alice")
        self.assertEqual(students[1].legal_first_name, "Bob")
        self.assertEqual(students[2].legal_first_name, "Charlie")

    def test_student_list_sorting_by_name_desc(self):
        # Sort by name desc
        response = self.client.get(reverse("student_list") + "?sort=name&dir=desc")
        students = list(response.context["students"])
        self.assertEqual(students[0].legal_first_name, "Charlie")
        self.assertEqual(students[1].legal_first_name, "Bob")
        self.assertEqual(students[2].legal_first_name, "Alice")

    def test_student_list_sorting_by_graduation_year(self):
        # Alice 2025, Charlie 2024, Bob 2026
        # asc: Charlie (2024), Alice (2025), Bob (2026)
        response = self.client.get(
            reverse("student_list") + "?sort=graduation_year&dir=asc"
        )
        students = list(response.context["students"])
        self.assertEqual(students[0].graduation_year, 2024)
        self.assertEqual(students[1].graduation_year, 2025)
        self.assertEqual(students[2].graduation_year, 2026)

    def test_adult_list_sorting_by_email(self):
        # Alice, Jane, John
        response = self.client.get(
            reverse("adult_list") + "?sort=personal_email&dir=asc"
        )
        adults = list(response.context["adults"])
        self.assertEqual(adults[0].personal_email, "alice@example.com")
        self.assertEqual(adults[1].personal_email, "jane@example.com")
        self.assertEqual(adults[2].personal_email, "john@example.com")

    def test_application_list_sorting_by_email(self):
        # APP1 (c@example.com), APP2 (a@example.com), APP3 (b@example.com)
        # asc: a, b, c
        response = self.client.get(
            reverse("application_review_list") + "?sort=email&dir=asc"
        )
        apps = list(response.context["applications"])
        self.assertEqual(apps[0].email, "a@example.com")
        self.assertEqual(apps[1].email, "b@example.com")
        self.assertEqual(apps[2].email, "c@example.com")
