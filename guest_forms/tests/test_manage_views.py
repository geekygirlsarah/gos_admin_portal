"""Tests for the guest form management views (create/edit/list)."""

from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from guest_forms.models import GuestForm


class GuestFormManageViewTests(TestCase):
    """Regression: the manage form must render the URL fields so saving works."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="leadmentor", password="pass12345"  # nosec B106
        )
        group, _ = Group.objects.get_or_create(name="LeadMentor")
        cls.user.groups.add(group)

    def setUp(self):
        self.client.login(username="leadmentor", password="pass12345")  # nosec B106

    def _pdf(self):
        return SimpleUploadedFile(
            "form.pdf", b"%PDF-1.4 fake pdf", content_type="application/pdf"
        )

    def _valid_post_data(self, **overrides):
        data = {
            "form_type": "student",
            "name": "Photo Release",
            "description": "",
            "is_required": "on",
            "display_order": "0",
            "is_active": "on",
            "legal_notices_url": "https://www.cmu.edu/legal/",
            "safety_guidelines_url": "",
        }
        data.update(overrides)
        return data

    def test_create_page_renders_url_fields(self):
        resp = self.client.get(reverse("guest_form_create"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'name="legal_notices_url"')
        self.assertContains(resp, 'name="safety_guidelines_url"')

    def test_edit_page_renders_url_fields(self):
        guest_form = GuestForm.objects.create(
            form_type="adult", name="Adult Waiver", file=self._pdf()
        )
        resp = self.client.get(reverse("guest_form_edit", args=[guest_form.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'name="legal_notices_url"')
        self.assertContains(resp, 'name="safety_guidelines_url"')

    def test_create_post_saves_guest_form(self):
        data = self._valid_post_data()
        data["file"] = self._pdf()
        resp = self.client.post(reverse("guest_form_create"), data)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("guest_form_manage_list"))
        self.assertTrue(GuestForm.objects.filter(name="Photo Release").exists())

    def test_update_post_saves_changes(self):
        guest_form = GuestForm.objects.create(
            form_type="student", name="Old Name", file=self._pdf()
        )
        data = self._valid_post_data(name="New Name")
        data["file"] = self._pdf()
        resp = self.client.post(reverse("guest_form_edit", args=[guest_form.pk]), data)
        self.assertEqual(resp.status_code, 302)
        guest_form.refresh_from_db()
        self.assertEqual(guest_form.name, "New Name")
