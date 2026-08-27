from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from programs.models import Adult


class AdultSortingTest(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="admin",
            password="password",
            email="admin@example.com",  # nosec B106
        )
        self.client.login(username="admin", password="password")  # nosec B106

        # Create adults with varying names
        # Sort by First (Preferred if exists) then Last
        # 1. Aaron (pref: Zander) Smith -> Zander Smith
        # 2. Bob (pref: None) Jones -> Bob Jones
        # 3. Charlie (pref: Alice) Brown -> Alice Brown

        Adult.objects.create(
            legal_first_name="Aaron", preferred_first_name="Zander", last_name="Smith"
        )
        Adult.objects.create(
            legal_first_name="Bob", preferred_first_name="", last_name="Jones"
        )
        Adult.objects.create(
            legal_first_name="Charlie", preferred_first_name="Alice", last_name="Brown"
        )

    def test_adults_list_sorting(self):
        """Test that AdultsListView sorts by preferred first name then last name."""
        response = self.client.get(
            reverse("adult_list"), {"sort": "name", "dir": "asc"}
        )
        self.assertEqual(response.status_code, 200)

        adults = response.context["adults"]
        names = [a.full_name for a in adults]

        # Expected order:
        # 1. Alice Brown (Charlie)
        # 2. Bob Jones
        # 3. Zander Smith (Aaron)

        expected_names = ["Alice Brown", "Bob Jones", "Zander Smith"]
        self.assertEqual(names, expected_names)

    def test_parent_list_sorting(self):
        Adult.objects.all().update(is_parent=True)
        response = self.client.get(
            reverse("parent_list"), {"sort": "name", "dir": "asc"}
        )
        self.assertEqual(response.status_code, 200)
        parents = response.context["parents"]
        names = [p.full_name for p in parents]
        expected_names = ["Alice Brown", "Bob Jones", "Zander Smith"]
        self.assertEqual(names, expected_names)

    def test_mentor_list_sorting(self):
        Adult.objects.all().update(is_mentor=True)
        response = self.client.get(
            reverse("mentor_list"), {"sort": "name", "dir": "asc"}
        )
        self.assertEqual(response.status_code, 200)
        mentors = response.context["mentors"]
        names = [m.full_name for m in mentors]
        expected_names = ["Alice Brown", "Bob Jones", "Zander Smith"]
        self.assertEqual(names, expected_names)

    def test_alumni_list_sorting(self):
        Adult.objects.all().update(is_alumni=True)
        response = self.client.get(
            reverse("alumni_list"), {"sort": "name", "dir": "asc"}
        )
        self.assertEqual(response.status_code, 200)
        alumni = response.context["alumni"]
        names = [a.full_name for a in alumni]
        expected_names = ["Alice Brown", "Bob Jones", "Zander Smith"]
        self.assertEqual(names, expected_names)
