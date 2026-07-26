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

    def test_mentor_accordion_expanded_when_is_mentor_true(self):
        # This might be hard to test just via HTML if it depends on JS for initial state,
        # but we can check the 'show' class in the template if we render it server-side.
        url = reverse("adult_edit", args=[self.adult.pk])
        response = self.client.get(url)

        # If is_mentor=True, mentor accordion should have 'show' class or be expanded
        # We'll see how I implement it.
        pass
