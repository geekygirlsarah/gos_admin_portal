from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from programs.models import Adult

User = get_user_model()


class AdultAccordionTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username="admin", password="password", email="admin@example.com"
        )  # nosec B106
        self.client.login(username="admin", password="password")  # nosec B106
        self.adult = Adult.objects.create(
            first_name="Test",
            last_name="Adult",
            is_mentor=True,
            is_parent=False,
            is_alumni=False,
        )

    def test_accordion_elements_present(self):
        url = reverse("adult_edit", args=[self.adult.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        # Check for accordion IDs
        self.assertContains(response, 'id="accordionAdult"')
        self.assertContains(response, 'id="accordionParent"')
        self.assertContains(response, 'id="accordionMentor"')
        self.assertContains(response, 'id="accordionAlumni"')

    def test_accordion_visibility(self):
        url = reverse("adult_edit", args=[self.adult.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        # Adult section should be visible
        self.assertContains(response, 'id="accordionAdult"')
        self.assertNotContains(response, 'accordion-item d-none" id="accordionAdult"')

        # Mentor section should be visible (is_mentor=True)
        self.assertContains(response, 'id="accordionMentor"')
        self.assertNotContains(response, 'accordion-item d-none" id="accordionMentor"')

        # Parent section should be hidden (is_parent=False)
        self.assertContains(response, 'accordion-item d-none" id="accordionParent"')

        # Alumni section should be hidden (is_alumni=False)
        self.assertContains(response, 'accordion-item d-none" id="accordionAlumni"')

    def test_csp_nonce_present(self):
        url = reverse("adult_edit", args=[self.adult.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        # Check for the script tag with nonce
        # Note: In tests, the nonce might be a dummy value depending on how django-csp is configured for testing,
        # but the attribute should be present.
        self.assertContains(response, '<script nonce="')
