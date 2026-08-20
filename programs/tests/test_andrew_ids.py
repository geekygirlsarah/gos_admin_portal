"""Tests for Andrew ID management view."""

from datetime import date

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from programs.models import Adult, Student
from programs.validators import validate_andrew_id


class AndrewIdValidatorTests(TestCase):
    def test_valid_andrew_id(self):
        validate_andrew_id("rriveter")
        validate_andrew_id("abc")
        validate_andrew_id("a1b2c3")

    def test_starts_with_digit(self):
        with self.assertRaises(ValidationError):
            validate_andrew_id("1riveter")

    def test_too_short(self):
        with self.assertRaises(ValidationError):
            validate_andrew_id("a")

    def test_special_characters(self):
        with self.assertRaises(ValidationError):
            validate_andrew_id("riveter!")

    def test_uppercase_normalized(self):
        validate_andrew_id("Rriveter")

    def test_empty_string_passes(self):
        validate_andrew_id("")

    def test_none_passes(self):
        validate_andrew_id(None)

    def test_with_suffix_rejected(self):
        with self.assertRaises(ValidationError):
            validate_andrew_id("rriveter@andrew.cmu.edu")


class AndrewIdManagementPermissionTests(TestCase):
    def setUp(self):
        self.password = "password123"  # nosec B105
        self.lead_user = User.objects.create_user(
            username="lead", password=self.password
        )
        Group.objects.get_or_create(name="LeadMentor")
        self.lead_user.groups.add(Group.objects.get(name="LeadMentor"))

        self.regular_user = User.objects.create_user(
            username="regular", password=self.password
        )

    def test_lead_mentor_can_access(self):
        self.client.login(username="lead", password=self.password)
        response = self.client.get(reverse("andrew_id_management"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Andrew ID Management")

    def test_regular_user_redirected(self):
        self.client.login(username="regular", password=self.password)
        response = self.client.get(reverse("andrew_id_management"))
        self.assertEqual(response.status_code, 302)

    def test_anonymous_redirected(self):
        response = self.client.get(reverse("andrew_id_management"))
        self.assertEqual(response.status_code, 302)


class AndrewIdManagementViewTests(TestCase):
    def setUp(self):
        self.password = "password123"  # nosec B105
        self.lead_user = User.objects.create_user(
            username="lead", password=self.password
        )
        Group.objects.get_or_create(name="LeadMentor")
        self.lead_user.groups.add(Group.objects.get(name="LeadMentor"))
        self.client.login(username="lead", password=self.password)

        self.student = Student.objects.create(
            first_name="Robert",
            last_name="Student",
            legal_first_name="Robert",
        )
        self.mentor = Adult.objects.create(
            first_name="Sarah",
            last_name="Mentor",
            is_mentor=True,
        )
        self.parent = Adult.objects.create(
            first_name="Alex",
            last_name="Parent",
            is_parent=True,
        )

    def test_search_students(self):
        response = self.client.get(reverse("andrew_id_management"), {"q": "Robert"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Robert Student")
        self.assertContains(response, "Student")

    def test_search_adults(self):
        response = self.client.get(reverse("andrew_id_management"), {"q": "Sarah"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sarah Mentor")

    def test_search_parents(self):
        response = self.client.get(reverse("andrew_id_management"), {"q": "Alex"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Alex Parent")

    def test_search_sorted_by_name(self):
        Student.objects.create(
            first_name="Zachary",
            last_name="Anderson",
            legal_first_name="Zachary",
        )
        Student.objects.create(
            first_name="Alice",
            last_name="Zhang",
            legal_first_name="Alice",
        )
        response = self.client.get(reverse("andrew_id_management"), {"q": ""})
        self.assertEqual(response.status_code, 200)

    def test_no_search_shows_assigned(self):
        self.student.andrew_id = "rstudent"
        self.student.andrew_email = "rstudent@andrew.cmu.edu"
        self.student.save()

        response = self.client.get(reverse("andrew_id_management"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Currently Assigned Andrew IDs")
        self.assertContains(response, "rstudent")
        self.assertContains(response, "Robert Student")

    def test_no_search_no_assigned(self):
        response = self.client.get(reverse("andrew_id_management"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No Andrew IDs have been assigned yet")

    def test_set_andrew_id_student(self):
        url = reverse("andrew_id_management") + "?q=Robert"
        response = self.client.post(
            url,
            {
                "action": "set",
                "person_type": "student",
                "person_id": self.student.pk,
                "andrew_id": "rstudent",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.student.refresh_from_db()
        self.assertEqual(self.student.andrew_id, "rstudent")
        self.assertEqual(self.student.andrew_email, "rstudent@andrew.cmu.edu")

    def test_set_andrew_id_adult(self):
        url = reverse("andrew_id_management") + "?q=Sarah"
        response = self.client.post(
            url,
            {
                "action": "set",
                "person_type": "adult",
                "person_id": self.mentor.pk,
                "andrew_id": "smentor",
                "andrew_id_expiration": "2027-01-15",
                "andrew_id_sponsor": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.mentor.refresh_from_db()
        self.assertEqual(self.mentor.andrew_id, "smentor")
        self.assertEqual(self.mentor.andrew_email, "smentor@andrew.cmu.edu")
        self.assertEqual(self.mentor.andrew_id_expiration, date(2027, 1, 15))

    def test_set_andrew_id_with_sponsor(self):
        sponsor = Adult.objects.create(
            first_name="Sponsor",
            last_name="Person",
            is_mentor=True,
            andrew_id="sponsor",
            andrew_email="sponsor@andrew.cmu.edu",
        )
        url = reverse("andrew_id_management") + "?q=Sarah"
        response = self.client.post(
            url,
            {
                "action": "set",
                "person_type": "adult",
                "person_id": self.mentor.pk,
                "andrew_id": "smentor",
                "andrew_id_expiration": "",
                "andrew_id_sponsor": str(sponsor.pk),
            },
        )
        self.assertEqual(response.status_code, 302)
        self.mentor.refresh_from_db()
        self.assertEqual(self.mentor.andrew_id_sponsor_id, sponsor.pk)

    def test_set_validates_format(self):
        url = reverse("andrew_id_management") + "?q=Robert"
        response = self.client.post(
            url,
            {
                "action": "set",
                "person_type": "student",
                "person_id": self.student.pk,
                "andrew_id": "1bad",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.student.refresh_from_db()
        self.assertIsNone(self.student.andrew_id)

    def test_set_validates_uniqueness_student(self):
        self.student.andrew_id = "taken"
        self.student.save()
        new_student = Student.objects.create(
            first_name="Other",
            last_name="Student",
            legal_first_name="Other",
        )
        url = reverse("andrew_id_management") + "?q=Other"
        response = self.client.post(
            url,
            {
                "action": "set",
                "person_type": "student",
                "person_id": new_student.pk,
                "andrew_id": "taken",
            },
        )
        self.assertEqual(response.status_code, 302)
        new_student.refresh_from_db()
        self.assertIsNone(new_student.andrew_id)

    def test_set_validates_uniqueness_across_types(self):
        self.student.andrew_id = "shared"
        self.student.save()
        url = reverse("andrew_id_management") + "?q=Sarah"
        response = self.client.post(
            url,
            {
                "action": "set",
                "person_type": "adult",
                "person_id": self.mentor.pk,
                "andrew_id": "shared",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.mentor.refresh_from_db()
        self.assertIsNone(self.mentor.andrew_id)

    def test_clear_andrew_id_student(self):
        self.student.andrew_id = "rstudent"
        self.student.andrew_email = "rstudent@andrew.cmu.edu"
        self.student.save()

        url = reverse("andrew_id_management") + "?q=Robert"
        response = self.client.post(
            url,
            {
                "action": "clear",
                "person_type": "student",
                "person_id": self.student.pk,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.student.refresh_from_db()
        self.assertIsNone(self.student.andrew_id)
        self.assertIsNone(self.student.andrew_email)

    def test_clear_andrew_id_adult(self):
        self.mentor.andrew_id = "smentor"
        self.mentor.andrew_email = "smentor@andrew.cmu.edu"
        self.mentor.andrew_id_expiration = date(2027, 6, 1)
        self.mentor.save()

        url = reverse("andrew_id_management") + "?q=Sarah"
        response = self.client.post(
            url,
            {
                "action": "clear",
                "person_type": "adult",
                "person_id": self.mentor.pk,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.mentor.refresh_from_db()
        self.assertIsNone(self.mentor.andrew_id)
        self.assertIsNone(self.mentor.andrew_email)
        self.assertIsNone(self.mentor.andrew_id_expiration)
        self.assertIsNone(self.mentor.andrew_id_sponsor)

    def test_empty_andrew_id_rejected(self):
        url = reverse("andrew_id_management") + "?q=Robert"
        response = self.client.post(
            url,
            {
                "action": "set",
                "person_type": "student",
                "person_id": self.student.pk,
                "andrew_id": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.student.refresh_from_db()
        self.assertIsNone(self.student.andrew_id)

    def test_assigned_list_sorted_by_name(self):
        self.student.andrew_id = "rstudent"
        self.student.save()
        self.mentor.andrew_id = "smentor"
        self.mentor.save()

        response = self.client.get(reverse("andrew_id_management"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        student_pos = content.index("Robert Student")
        mentor_pos = content.index("Sarah Mentor")
        self.assertLess(student_pos, mentor_pos)
