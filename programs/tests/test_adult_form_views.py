"""
TDD tests for the Add/Edit Adult form improvements:
  1. ParentCreateView must allow duplicate emails (no unique-email error).
  2. AdultCreateView (adults/new/) must exist and be accessible.
  3. AdultForm must include mentor-specific and alumni-specific fields.
  4. adults/form.html must render role-specific sections.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from programs.forms import AdultForm
from programs.models import Adult

User = get_user_model()


def _make_staff(username="staff"):
    password = "pass12345"  # nosec B105
    user = User.objects.create_superuser(
        username=username, password=password, email=f"{username}@example.com"
    )
    return user, password


class ParentFormDuplicateEmailTest(TestCase):
    """ParentForm must not raise a validation error when two adults share an email."""

    def test_parent_form_allows_duplicate_email(self):
        # Pre-existing adult with the shared email
        Adult.objects.create(
            legal_first_name="Mary",
            last_name="Smith",
            personal_email="shared@example.com",
            is_parent=True,
        )
        # A second adult form submission with the same email should be valid
        form = AdultForm(
            data={
                "legal_first_name": "John",
                "last_name": "Smith",
                "personal_email": "shared@example.com",
                "email_updates": False,
                "is_parent": True,
                "is_mentor": False,
                "is_alumni": False,
                "login_enabled": True,
            }
        )
        self.assertTrue(
            form.is_valid(),
            f"AdultForm should allow duplicate email but got errors: {form.errors}",
        )

    def test_adult_form_allows_duplicate_email(self):
        Adult.objects.create(
            legal_first_name="Mary",
            last_name="Smith",
            personal_email="shared@example.com",
            is_parent=True,
        )
        form = AdultForm(
            data={
                "legal_first_name": "John",
                "last_name": "Smith",
                "personal_email": "shared@example.com",
                "email_updates": False,
                "is_parent": True,
                "is_mentor": False,
                "is_alumni": False,
                "login_enabled": True,
            }
        )
        self.assertTrue(
            form.is_valid(),
            f"AdultForm should allow duplicate email but got errors: {form.errors}",
        )


class AdultCreateViewTest(TestCase):
    """adults/new/ must exist, require login, and create an Adult on POST."""

    def setUp(self):
        self.user, self.password = _make_staff()
        self.client.login(username="staff", password=self.password)

    def test_get_adult_create_view(self):
        url = reverse("adult_create")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Add Adult")

    def test_post_adult_create_view_creates_adult(self):
        url = reverse("adult_create")
        response = self.client.post(
            url,
            {
                "legal_first_name": "Jane",
                "last_name": "Doe",
                "personal_email": "jane@example.com",
                "email_updates": False,
                "is_parent": True,
                "is_mentor": False,
                "is_alumni": False,
                "login_enabled": True,
            },
        )
        # Should redirect on success
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            Adult.objects.filter(personal_email="jane@example.com").exists()
        )

    def test_post_adult_create_duplicate_email_allowed(self):
        """Two adults with the same email must both be creatable via the view."""
        Adult.objects.create(
            legal_first_name="Mary",
            last_name="Smith",
            personal_email="shared@example.com",
            is_parent=True,
        )
        url = reverse("adult_create")
        response = self.client.post(
            url,
            {
                "legal_first_name": "John",
                "last_name": "Smith",
                "personal_email": "shared@example.com",
                "email_updates": False,
                "is_parent": True,
                "is_mentor": False,
                "is_alumni": False,
                "login_enabled": True,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            Adult.objects.filter(personal_email="shared@example.com").count(), 2
        )


class AdultFormFieldsTest(TestCase):
    """AdultForm must include mentor-specific and alumni-specific fields."""

    def test_adult_form_has_mentor_fields(self):
        form = AdultForm()
        mentor_fields = [
            "start_year",
            "role",
            "emergency_contact_name",
            "emergency_contact_phone",
            "on_discord",
            "discord_username",
        ]
        for field in mentor_fields:
            self.assertIn(
                field,
                form.fields,
                f"AdultForm is missing mentor field: {field}",
            )

    def test_adult_form_no_clearance_fields(self):
        form = AdultForm()
        for field in (
            "has_paca_clearance",
            "has_patch_clearance",
            "has_fbi_clearance",
            "pa_clearances_expiration_date",
        ):
            self.assertNotIn(
                field,
                form.fields,
                f"AdultForm should not contain removed clearance field: {field}",
            )

    def test_adult_form_has_alumni_fields(self):
        form = AdultForm()
        alumni_fields = [
            "college",
            "field_of_study",
            "employer",
            "job_title",
            "ok_to_contact",
        ]
        for field in alumni_fields:
            self.assertIn(
                field, form.fields, f"AdultForm is missing alumni field: {field}"
            )


class AdultEditViewRoleFieldsTest(TestCase):
    """The adults/form.html template must render mentor and alumni sections."""

    def setUp(self):
        self.user, self.password = _make_staff()
        self.client.login(username="staff", password=self.password)
        self.adult = Adult.objects.create(
            legal_first_name="Test",
            last_name="Adult",
            is_mentor=True,
        )

    def test_edit_page_shows_mentor_section(self):
        url = reverse("adult_edit", args=[self.adult.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        # Mentor section should be present
        self.assertContains(response, "Clearances")

    def test_edit_page_shows_alumni_section(self):
        url = reverse("adult_edit", args=[self.adult.pk])
        response = self.client.get(url)
        self.assertContains(response, "Alumni")

    def test_create_page_title_says_add(self):
        url = reverse("adult_create")
        response = self.client.get(url)
        self.assertContains(response, "Add Adult")
