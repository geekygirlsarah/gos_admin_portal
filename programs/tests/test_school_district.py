from django.contrib.auth.models import Permission, User
from django.test import Client, TestCase
from django.urls import reverse

from programs.models import School, SchoolDistrict


class DistrictModelTest(TestCase):
    def test_str(self):
        d = SchoolDistrict.objects.create(name="North Allegheny School District")
        self.assertEqual(str(d), "North Allegheny School District")

    def test_school_fk_links_to_district(self):
        d = SchoolDistrict.objects.create(name="North Allegheny School District")
        school = School.objects.create(name="North Allegheny High School", district=d)
        self.assertEqual(school.district, d)
        self.assertEqual(list(d.schools.all()), [school])

    def test_unique_name(self):
        SchoolDistrict.objects.create(name="Plum Borough School District")
        with self.assertRaises(Exception):
            SchoolDistrict.objects.create(name="Plum Borough School District")


class DistrictCrudTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="admin",
            password="password",
        )  # nosec B106
        for codename in (
            "add_schooldistrict",
            "change_schooldistrict",
            "delete_schooldistrict",
        ):
            perm = Permission.objects.get(codename=codename)
            self.user.user_permissions.add(perm)
        self.client = Client()
        self.client.force_login(self.user)

    def test_list_districts(self):
        SchoolDistrict.objects.create(name="PPS")
        SchoolDistrict.objects.create(name="Steel Valley")
        response = self.client.get(reverse("school_district_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "PPS")
        self.assertContains(response, "Steel Valley")

    def test_create_district(self):
        response = self.client.post(
            reverse("school_district_create"), {"name": "Moon Area School District"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            SchoolDistrict.objects.filter(name="Moon Area School District").exists()
        )

    def test_create_district_form_renders(self):
        response = self.client.get(reverse("school_district_create"))
        self.assertEqual(response.status_code, 200)

    def test_update_district(self):
        d = SchoolDistrict.objects.create(name="Old Name")
        response = self.client.post(
            reverse("school_district_edit", args=[d.pk]), {"name": "New Name"}
        )
        self.assertEqual(response.status_code, 302)
        d.refresh_from_db()
        self.assertEqual(d.name, "New Name")

    def test_delete_district_detaches_schools(self):
        d = SchoolDistrict.objects.create(name="North Allegheny School District")
        school = School.objects.create(name="NA High School", district=d)
        response = self.client.post(reverse("school_district_delete", args=[d.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(SchoolDistrict.objects.filter(pk=d.pk).exists())
        school.refresh_from_db()
        self.assertIsNone(school.district)

    def test_delete_district_confirm_page_shows_schools(self):
        d = SchoolDistrict.objects.create(name="North Allegheny School District")
        school = School.objects.create(name="NA High School", district=d)
        response = self.client.get(reverse("school_district_delete", args=[d.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, d.name)
        self.assertContains(response, school.name)
