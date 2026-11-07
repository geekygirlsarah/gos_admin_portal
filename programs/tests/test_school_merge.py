from django.contrib.auth.models import Permission, User
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from audit.events import AuditEvent
from audit.models import AuditLog
from organizations.models import Organization, UserOrganizationMembership
from programs.forms import SchoolMergeForm
from programs.models import School, SchoolDistrict, Student


class SchoolMergeTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="admin",
            password="password",
        )  # nosec B106
        UserOrganizationMembership.objects.create(
            user=self.user,
            organization=Organization.objects.get(slug="gos"),
            role="Mentor",
        )
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
        district = SchoolDistrict.objects.create(name="Plum Borough SD")
        keep = self._school("Plum Senior High School", district=district)
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
        self.assertEqual(keep.district, district)
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


class SchoolMergeOrganizationIsolationTest(TestCase):
    def setUp(self):
        self.org = Organization.objects.get(slug="gos")
        self.other_org = Organization.objects.create(name="Other", slug="other")
        self.user = User.objects.create_user(username="school_merger")
        UserOrganizationMembership.objects.create(
            user=self.user, organization=self.org, role="Mentor"
        )
        self.user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="programs", codename="change_school"
            )
        )
        self.client.force_login(self.user)
        self.local_district = SchoolDistrict.objects.create(
            organization=self.org, name="Local District"
        )
        self.other_district = SchoolDistrict.objects.create(
            organization=self.other_org, name="Foreign District"
        )
        self.keep = School.objects.create(organization=self.org, name="Local Keep")
        self.source = School.objects.create(
            organization=self.org, name="Local Source", district=self.local_district
        )
        self.foreign = School.objects.create(
            organization=self.other_org, name="Foreign School", city="Foreign City"
        )
        self.student = Student.objects.create(
            organization=self.org,
            legal_first_name="Test",
            last_name="Student",
            graduation_year=2026,
            school=self.source,
        )

    def test_merge_page_and_choices_hide_foreign_schools_and_districts(self):
        self.source.district = self.other_district
        self.source.save(update_fields=["district"])
        response = self.client.get(reverse("school_merge"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Local Keep")
        self.assertContains(response, "Local Source")
        self.assertNotContains(response, "Foreign School")
        self.assertNotContains(response, "Foreign District")
        for field in ("keep", "source"):
            self.assertEqual(
                set(response.context["form"].fields[field].queryset),
                {self.keep, self.source},
            )

    def test_foreign_keep_and_source_are_rejected_without_changes_or_audit(self):
        for keep, source, field in (
            (self.foreign, self.source, "keep"),
            (self.keep, self.foreign, "source"),
        ):
            with self.subTest(field=field):
                response = self.client.post(
                    reverse("school_merge"), {"keep": keep.pk, "source": source.pk}
                )
                self.assertEqual(response.status_code, 200)
                self.assertIn(field, response.context["form"].errors)
                self.assertTrue(School.objects.filter(pk=keep.pk).exists())
                self.assertTrue(School.objects.filter(pk=source.pk).exists())
                self.keep.refresh_from_db()
                self.foreign.refresh_from_db()
                self.student.refresh_from_db()
                self.assertIsNone(self.keep.city)
                self.assertEqual(self.foreign.city, "Foreign City")
                self.assertEqual(self.student.school_id, self.source.pk)
                self.assertFalse(
                    AuditLog.objects.filter(event=AuditEvent.RECORDS_MERGED).exists()
                )

    def test_global_form_still_rejects_schools_from_different_organizations(self):
        form = SchoolMergeForm({"keep": self.keep.pk, "source": self.foreign.pk})
        self.assertFalse(form.is_valid())

    @override_settings(ALLOWED_HOSTS=["testserver", "other.localhost"])
    def test_other_organization_can_merge_its_own_schools(self):
        UserOrganizationMembership.objects.create(
            user=self.user, organization=self.other_org, role="Mentor"
        )
        other_source = School.objects.create(
            organization=self.other_org, name="Other Source", city="Other City"
        )
        response = self.client.get(reverse("school_merge"), HTTP_HOST="other.localhost")
        self.assertContains(response, "Foreign School")
        self.assertNotContains(response, "Local Source")
        response = self.client.post(
            reverse("school_merge"),
            {"keep": self.foreign.pk, "source": other_source.pk},
            HTTP_HOST="other.localhost",
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(School.objects.filter(pk=other_source.pk).exists())
        self.assertTrue(School.objects.filter(pk=self.source.pk).exists())

    def test_merge_rejects_foreign_student_link_without_changes(self):
        foreign_student = Student.objects.create(
            organization=self.other_org,
            legal_first_name="Other",
            last_name="Student",
            graduation_year=2026,
            school=self.source,
        )
        response = self.client.post(
            reverse("school_merge"), {"keep": self.keep.pk, "source": self.source.pk}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "The source school has conflicting links.")
        self.assertTrue(School.objects.filter(pk=self.source.pk).exists())
        self.student.refresh_from_db()
        foreign_student.refresh_from_db()
        self.keep.refresh_from_db()
        self.assertEqual(self.student.school_id, self.source.pk)
        self.assertEqual(foreign_student.school_id, self.source.pk)
        self.assertIsNone(self.keep.district_id)
        self.assertFalse(
            AuditLog.objects.filter(event=AuditEvent.RECORDS_MERGED).exists()
        )

    def test_merge_rejects_foreign_source_district_without_changes(self):
        self.source.district = self.other_district
        self.source.save(update_fields=["district"])
        response = self.client.post(
            reverse("school_merge"), {"keep": self.keep.pk, "source": self.source.pk}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "The source school has conflicting links.")
        self.assertTrue(School.objects.filter(pk=self.source.pk).exists())
        self.keep.refresh_from_db()
        self.student.refresh_from_db()
        self.assertIsNone(self.keep.district_id)
        self.assertEqual(self.student.school_id, self.source.pk)
        self.assertFalse(
            AuditLog.objects.filter(event=AuditEvent.RECORDS_MERGED).exists()
        )

    def test_local_merge_preserves_audit_and_reassigns_students(self):
        response = self.client.post(
            reverse("school_merge"), {"keep": self.keep.pk, "source": self.source.pk}
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(School.objects.filter(pk=self.source.pk).exists())
        self.keep.refresh_from_db()
        self.student.refresh_from_db()
        self.assertEqual(self.keep.district_id, self.local_district.pk)
        self.assertEqual(self.student.school_id, self.keep.pk)
        log = AuditLog.objects.get(event=AuditEvent.RECORDS_MERGED, actor=self.user)
        self.assertEqual(log.before["source_pk"], self.source.pk)
        self.assertEqual(log.after["keep_pk"], self.keep.pk)
