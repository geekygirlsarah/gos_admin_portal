from django.contrib.auth.models import Permission, User
from django.test import Client, TestCase
from django.urls import reverse

from programs.models import School, Student


class SchoolMergeTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="admin",
            password="password",
        )  # nosec B106
        perm = Permission.objects.get(codename="change_school")
        self.user.user_permissions.add(perm)
        self.client = Client()
        self.client.force_login(self.user)

    def _school(self, name, **kwargs):
        return School.objects.create(name=name, **kwargs)

    def _student(self, school, **kwargs):
        defaults = {
            "legal_first_name": "Test",
            "last_name": "Student",
            "school": school,
            "graduation_year": 2026,
        }
        defaults.update(kwargs)
        return Student.objects.create(**defaults)

    def test_merge_reassigns_students_and_deletes_source(self):
        keep = self._school("Plum Senior High School")
        source = self._school("Plum SHS")
        s1 = self._student(keep)
        s2 = self._student(source)

        response = self.client.post(
            reverse("school_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(School.objects.filter(pk=source.pk).exists())
        s1.refresh_from_db()
        s2.refresh_from_db()
        self.assertEqual(s1.school_id, keep.pk)
        self.assertEqual(s2.school_id, keep.pk)

    def test_merge_preserves_keep_contact_data(self):
        keep = self._school("Plum Senior High School", district="Plum Borough SD")
        source = self._school(
            "Plum SHS",
            street_address="900 Common Rd",
            city="Pittsburgh",
            zip_code="15239",
        )

        self.client.post(
            reverse("school_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        keep.refresh_from_db()
        self.assertEqual(keep.district, "Plum Borough SD")
        self.assertEqual(keep.street_address, "900 Common Rd")
        self.assertEqual(keep.city, "Pittsburgh")
        self.assertEqual(keep.zip_code, "15239")

    def test_merge_does_not_overwrite_keep_contact_data(self):
        keep = self._school("Plum Senior High School", city="Pittsburgh")
        source = self._school("Plum SHS", city="Pittsburg", zip_code="15239")

        self.client.post(
            reverse("school_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        keep.refresh_from_db()
        self.assertEqual(keep.city, "Pittsburgh")
        self.assertEqual(keep.zip_code, "15239")

    def test_cannot_merge_school_into_itself(self):
        keep = self._school("Plum Senior High School")

        response = self.client.post(
            reverse("school_merge"),
            {"keep": keep.pk, "source": keep.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(School.objects.filter(pk=keep.pk).exists())

    def test_merge_page_lists_schools_with_keep_and_source_options(self):
        school_a = self._school("Plum Senior High School", city="Pittsburgh")
        school_b = self._school("Plum SHS")

        response = self.client.get(reverse("school_merge"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="keep" value="%s"' % school_a.pk)
        self.assertContains(response, 'name="source" value="%s"' % school_b.pk)
        self.assertContains(response, "Plum Senior High School")
        self.assertContains(response, "Plum SHS")
