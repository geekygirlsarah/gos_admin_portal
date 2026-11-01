"""Tests for Organization Email / Account management view."""

from datetime import date

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from programs.models import Adult, Student
from programs.validators import validate_andrew_id, validate_org_account_id


class OrgAccountIdValidatorTests(TestCase):
    def test_valid_org_account_id(self):
        validate_org_account_id("rriveter")
        validate_org_account_id("abc")
        validate_org_account_id("a1b2c3")
        validate_org_account_id("jane.doe")
        validate_org_account_id("john_doe")
        validate_org_account_id("student-123")

    def test_starts_with_digit_rejected(self):
        with self.assertRaises(ValidationError):
            validate_org_account_id("1riveter")

    def test_too_short(self):
        with self.assertRaises(ValidationError):
            validate_org_account_id("a")

    def test_special_characters_rejected(self):
        with self.assertRaises(ValidationError):
            validate_org_account_id("riveter!")

    def test_uppercase_normalized(self):
        validate_org_account_id("Rriveter")

    def test_empty_string_passes(self):
        validate_org_account_id("")

    def test_none_passes(self):
        validate_org_account_id(None)


class OrgEmailManagementPermissionTests(TestCase):
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
        response = self.client.get(reverse("org_email_management"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Management")

    def test_backward_compat_url_works(self):
        self.client.login(username="lead", password=self.password)
        response = self.client.get(reverse("andrew_id_management"))
        self.assertEqual(response.status_code, 200)

    def test_regular_user_redirected(self):
        self.client.login(username="regular", password=self.password)
        response = self.client.get(reverse("org_email_management"))
        self.assertEqual(response.status_code, 302)

    def test_anonymous_redirected(self):
        response = self.client.get(reverse("org_email_management"))
        self.assertEqual(response.status_code, 302)


class OrgEmailManagementViewTests(TestCase):
    def setUp(self):
        self.password = "password123"  # nosec B105
        self.lead_user = User.objects.create_user(
            username="lead", password=self.password
        )
        Group.objects.get_or_create(name="LeadMentor")
        self.lead_user.groups.add(Group.objects.get(name="LeadMentor"))
        self.client.login(username="lead", password=self.password)

        self.student = Student.objects.create(
            preferred_first_name="Robert",
            last_name="Student",
            legal_first_name="Robert",
        )
        self.mentor = Adult.objects.create(
            legal_first_name="Sarah",
            last_name="Mentor",
            is_mentor=True,
        )
        self.parent = Adult.objects.create(
            legal_first_name="Alex",
            last_name="Parent",
            is_parent=True,
        )

    def test_search_students(self):
        response = self.client.get(reverse("org_email_management"), {"q": "Robert"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Robert Student")
        self.assertContains(response, "Student")

    def test_search_adults(self):
        response = self.client.get(reverse("org_email_management"), {"q": "Sarah"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sarah Mentor")

    def test_set_student_org_account(self):
        response = self.client.post(
            reverse("org_email_management"),
            {
                "action": "set",
                "person_type": "student",
                "person_id": self.student.pk,
                "org_account_id": "rstudent",
                "org_email_expiration": "2026-12-31",
                "org_email_sponsor": self.mentor.pk,
            },
        )
        self.assertEqual(response.status_code, 302)

        self.student.refresh_from_db()
        self.assertEqual(self.student.org_account_id, "rstudent")
        self.assertEqual(self.student.andrew_id, "rstudent")
        self.assertTrue(self.student.org_email.startswith("rstudent@"))
        self.assertEqual(self.student.org_email_expiration, date(2026, 12, 31))
        self.assertEqual(self.student.org_email_sponsor, self.mentor)

    def test_set_adult_org_account(self):
        response = self.client.post(
            reverse("org_email_management"),
            {
                "action": "set",
                "person_type": "adult",
                "person_id": self.mentor.pk,
                "org_account_id": "smentor",
            },
        )
        self.assertEqual(response.status_code, 302)

        self.mentor.refresh_from_db()
        self.assertEqual(self.mentor.org_account_id, "smentor")
        self.assertEqual(self.mentor.andrew_id, "smentor")
        self.assertTrue(self.mentor.org_email.startswith("smentor@"))

    def test_clear_org_account(self):
        self.student.org_account_id = "rstudent"
        self.student.org_email = "rstudent@andrew.cmu.edu"
        self.student.org_email_expiration = date(2026, 12, 31)
        self.student.org_email_sponsor = self.mentor
        self.student.save()

        response = self.client.post(
            reverse("org_email_management"),
            {
                "action": "clear",
                "person_type": "student",
                "person_id": self.student.pk,
            },
        )
        self.assertEqual(response.status_code, 302)

        self.student.refresh_from_db()
        self.assertIsNone(self.student.org_account_id)
        self.assertIsNone(self.student.andrew_id)
        self.assertIsNone(self.student.org_email)
        self.assertIsNone(self.student.org_email_expiration)
        self.assertIsNone(self.student.org_email_sponsor)

    def test_duplicate_org_account_rejected(self):
        self.student.org_account_id = "rriveter"
        self.student.save()

        response = self.client.post(
            reverse("org_email_management"),
            {
                "action": "set",
                "person_type": "adult",
                "person_id": self.mentor.pk,
                "org_account_id": "rriveter",
            },
        )
        self.assertEqual(response.status_code, 302)

        self.mentor.refresh_from_db()
        self.assertIsNone(self.mentor.org_account_id)
