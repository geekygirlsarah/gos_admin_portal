"""Tests for API key management form and views."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from api.models import ApiClientKey

User = get_user_model()


class ApiClientKeyFormTest(TestCase):
    """The form should auto-generate a key without any user input."""

    def test_create_generates_key_automatically(self):
        """Saving a new key with only name+scope should produce a non-empty key."""
        from api.forms import ApiClientKeyForm

        form = ApiClientKeyForm(
            data={"name": "Test Key", "scope": "read", "is_active": True}
        )
        self.assertTrue(form.is_valid(), form.errors)
        obj = form.save()
        self.assertTrue(obj.key, "Key should be auto-generated")
        self.assertEqual(len(obj.key), 64)

    def test_edit_preserves_existing_key(self):
        """Editing an existing key without changing it should keep the same key."""
        from api.forms import ApiClientKeyForm

        existing = ApiClientKey.objects.create(
            name="Existing", scope="read", key="a" * 64
        )
        form = ApiClientKeyForm(
            data={"name": "Existing", "scope": "write", "is_active": True},
            instance=existing,
        )
        self.assertTrue(form.is_valid(), form.errors)
        obj = form.save()
        self.assertEqual(obj.key, "a" * 64, "Key should be preserved on edit")

    def test_form_has_no_key_input_field(self):
        """The form should not expose a raw key input field to users."""
        from api.forms import ApiClientKeyForm

        form = ApiClientKeyForm()
        self.assertNotIn("key", form.fields)
        self.assertNotIn("generate_new_key", form.fields)


class ApiKeyCreateViewTest(TestCase):
    """Creating an API key via the UI should auto-generate the key."""

    def setUp(self):
        self.user = User.objects.create_user(  # nosec B106
            username="staff", password="testpass123"
        )
        for perm in ["add_apiclientkey", "view_apiclientkey", "change_apiclientkey"]:
            self.user.user_permissions.add(Permission.objects.get(codename=perm))
        self.client.force_login(self.user)

    def test_create_via_view_generates_key(self):
        """POST to create view with only name+scope should create a key with auto-generated value."""
        response = self.client.post(
            reverse("api_key_create"),
            {"name": "Kiosk Key", "scope": "write", "is_active": True},
        )
        self.assertIn(response.status_code, [200, 302])
        key = ApiClientKey.objects.filter(name="Kiosk Key").first()
        self.assertIsNotNone(key)
        self.assertEqual(len(key.key), 64)

    def test_edit_view_shows_existing_key_readonly(self):
        """GET to edit view should return 200 and not include a writable key input."""
        existing = ApiClientKey.objects.create(
            name="My Key", scope="read", key="b" * 64
        )
        response = self.client.get(reverse("api_key_edit", args=[existing.pk]))
        self.assertEqual(response.status_code, 200)
        # The key value should be visible (read-only display) but not in a writable input
        self.assertContains(response, "b" * 64)
        # Should not have an editable input named "key"
        self.assertNotContains(response, 'name="key"')
