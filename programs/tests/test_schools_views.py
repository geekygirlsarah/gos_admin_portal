from unittest.mock import patch

from django.contrib.auth.models import Group, Permission, User
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse

from audit.events import AuditEvent
from audit.models import AuditLog
from organizations.models import Organization, UserOrganizationMembership
from programs.models import Program, ProgramFeature, School, SchoolDistrict
from programs.views.schools import DistrictListView, SchoolListView


class SchoolViewsTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="lead_mentor",
            email="lead@example.com",
            password="password123",  # nosec B106
        )
        UserOrganizationMembership.objects.create(
            user=self.user,
            organization=Organization.objects.get(slug="gos"),
            role="LeadMentor",
        )
        lead_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.user.groups.add(lead_group)
        for codename in (
            "add_school",
            "change_school",
            "delete_school",
            "add_schooldistrict",
            "change_schooldistrict",
            "delete_schooldistrict",
        ):
            perm = Permission.objects.filter(codename=codename).first()
            if perm:
                self.user.user_permissions.add(perm)

        self.client = Client()
        self.client.force_login(self.user)

        self.district = SchoolDistrict.objects.create(name="Pittsburgh Public Schools")
        self.school1 = School.objects.create(
            name="Allderdice High School",
            district=self.district,
            city="Pittsburgh",
            state="PA",
            zip_code="15217",
        )
        self.school2 = School.objects.create(
            name="Independent Prep Academy",
            district=None,
            city="Pittsburgh",
            state="PA",
            zip_code="15213",
        )

    def test_school_list_view_renders_and_has_kpis(self):
        response = self.client.get(reverse("school_list"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "schools/list.html")
        self.assertContains(response, "Allderdice High School")
        self.assertContains(response, "Independent Prep Academy")
        self.assertContains(response, "Pittsburgh Public Schools")
        self.assertEqual(response.context["total_schools_count"], 2)
        self.assertEqual(response.context["districts_count"], 1)
        self.assertEqual(response.context["assigned_schools_count"], 1)
        self.assertEqual(response.context["unassigned_schools_count"], 1)

    def test_school_district_list_view_renders_and_has_kpis(self):
        response = self.client.get(reverse("school_district_list"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "schools/district_list.html")
        self.assertContains(response, "Pittsburgh Public Schools")
        self.assertEqual(response.context["districts_count"], 1)
        self.assertEqual(response.context["school_count"], 1)
        self.assertEqual(response.context["unspecified_school_count"], 1)

    def test_school_forms_and_confirm_pages_render(self):
        # Create school form
        response = self.client.get(reverse("school_create"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "schools/form.html")

        # Edit school form
        response = self.client.get(reverse("school_edit", args=[self.school1.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "schools/form.html")

        # Merge school form
        response = self.client.get(reverse("school_merge"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "schools/merge.html")

        # Create district form
        response = self.client.get(reverse("school_district_create"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "schools/district_form.html")

        # Edit district form
        response = self.client.get(
            reverse("school_district_edit", args=[self.district.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "schools/district_form.html")

        # Delete district confirm
        response = self.client.get(
            reverse("school_district_delete", args=[self.district.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "schools/district_confirm_delete.html")


class SchoolOrganizationIsolationTest(TestCase):
    def setUp(self):
        self.org = Organization.objects.get(slug="gos")
        self.other_org = Organization.objects.create(name="Other", slug="other")
        self.user = User.objects.create_user(username="school_manager")
        for organization in (self.org, self.other_org):
            UserOrganizationMembership.objects.create(
                user=self.user, organization=organization, role="Mentor"
            )
        self.user.user_permissions.add(
            *Permission.objects.filter(
                content_type__app_label="programs",
                codename__in=(
                    "add_school",
                    "change_school",
                    "add_schooldistrict",
                    "change_schooldistrict",
                    "delete_schooldistrict",
                ),
            )
        )
        self.client.force_login(self.user)
        self.district = SchoolDistrict.objects.create(
            organization=self.org, name="Local District"
        )
        self.other_district = SchoolDistrict.objects.create(
            organization=self.other_org, name="Foreign District"
        )
        self.local_school = School.objects.create(
            organization=self.org, name="Local School", district=self.district
        )
        self.unassigned = School.objects.create(
            organization=self.org, name="Unassigned School"
        )
        self.cross_linked = School.objects.create(
            organization=self.org,
            name="Cross-linked School",
            district=self.other_district,
        )
        self.other_school = School.objects.create(
            organization=self.other_org, name="Foreign School", district=self.district
        )
        School.objects.create(organization=self.other_org, name="Foreign Unassigned")

    def test_school_forms_limit_district_choices(self):
        for url in (
            reverse("school_create"),
            reverse("school_edit", args=[self.local_school.pk]),
            reverse("school_edit", args=[self.cross_linked.pk]),
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Local District")
                self.assertNotContains(response, "Foreign District")
                self.assertEqual(
                    set(response.context["form"].fields["district"].queryset),
                    {self.district},
                )

    def test_school_form_rejects_foreign_district_on_create_and_edit(self):
        for url, name in (
            (reverse("school_create"), "New School"),
            (reverse("school_edit", args=[self.local_school.pk]), "Changed School"),
        ):
            with self.subTest(url=url):
                response = self.client.post(
                    url, {"name": name, "district": self.other_district.pk}
                )
                self.assertEqual(response.status_code, 200)
                self.assertIn("district", response.context["form"].errors)
        self.assertFalse(School.objects.filter(name="New School").exists())
        self.local_school.refresh_from_db()
        self.assertEqual(self.local_school.name, "Local School")
        self.assertEqual(self.local_school.district_id, self.district.pk)

    @override_settings(ALLOWED_HOSTS=["testserver", "other.localhost"])
    def test_school_form_saves_local_district_and_organization(self):
        response = self.client.post(
            reverse("school_create"),
            {"name": "Other New School", "district": self.other_district.pk},
            HTTP_HOST="other.localhost",
        )
        self.assertEqual(response.status_code, 302)
        school = School.objects.get(name="Other New School")
        self.assertEqual(school.organization_id, self.other_org.pk)
        self.assertEqual(school.district_id, self.other_district.pk)
        response = self.client.post(
            reverse("school_edit", args=[self.unassigned.pk]),
            {"name": self.unassigned.name, "district": self.district.pk},
        )
        self.assertEqual(response.status_code, 302)
        self.unassigned.refresh_from_db()
        self.assertEqual(self.unassigned.district_id, self.district.pk)

    @override_settings(ALLOWED_HOSTS=["testserver", "other.localhost"])
    def test_district_create_assigns_active_organization_and_limits_visibility(self):
        create_url = reverse("school_district_create")
        response = self.client.get(create_url, HTTP_HOST="other.localhost")
        self.assertEqual(response.status_code, 200)
        with self.assertLogs("programs.forms", level="INFO") as logs:
            response = self.client.post(
                create_url,
                {"name": "New Other District"},
                HTTP_HOST="other.localhost",
            )
        self.assertRedirects(
            response, reverse("school_district_list"), fetch_redirect_response=False
        )
        district = SchoolDistrict.objects.get(name="New Other District")
        self.assertEqual(district.organization_id, self.other_org.pk)
        self.assertTrue(any("SchoolDistrict[" in line for line in logs.output))

        list_url = reverse("school_district_list")
        response = self.client.get(list_url, HTTP_HOST="other.localhost")
        self.assertContains(response, "New Other District")
        self.assertIn(district, response.context["districts"])
        response = self.client.get(list_url)
        self.assertNotContains(response, "New Other District")
        self.assertNotIn(district, response.context["districts"])

    def test_school_directory_is_scoped_including_totals_and_related_district(self):
        response = self.client.get(reverse("school_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Local School")
        self.assertContains(response, "Cross-linked School")
        self.assertContains(response, "Local District")
        self.assertNotContains(response, "Foreign School")
        self.assertNotContains(response, "Foreign District")
        self.assertEqual(
            set(response.context["schools"]),
            {self.local_school, self.unassigned, self.cross_linked},
        )
        self.assertEqual(response.context["total_schools_count"], 3)
        self.assertEqual(response.context["districts_count"], 1)
        self.assertEqual(response.context["assigned_schools_count"], 2)
        self.assertEqual(response.context["unassigned_schools_count"], 1)

    def test_district_directory_is_scoped_including_all_counts(self):
        response = self.client.get(reverse("school_district_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Local District")
        self.assertNotContains(response, "Foreign District")
        districts = list(response.context["districts"])
        self.assertEqual([d.pk for d in districts], [self.district.pk])
        self.assertEqual(districts[0].school_count, 1)
        self.assertEqual(response.context["districts_count"], 1)
        self.assertEqual(response.context["school_count"], 2)
        self.assertEqual(response.context["unspecified_school_count"], 1)

    @override_settings(ALLOWED_HOSTS=["testserver", "other.localhost"])
    def test_other_organization_sees_only_its_directory(self):
        response = self.client.get(reverse("school_list"), HTTP_HOST="other.localhost")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Foreign School")
        self.assertNotContains(response, "Local School")
        self.assertNotContains(response, "Local District")
        self.assertEqual(response.context["total_schools_count"], 2)
        self.assertEqual(response.context["assigned_schools_count"], 1)
        response = self.client.get(
            reverse("school_district_list"), HTTP_HOST="other.localhost"
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Foreign District")
        self.assertNotContains(response, "Local District")
        self.assertEqual(response.context["districts_count"], 1)
        self.assertEqual(list(response.context["districts"])[0].school_count, 0)

    def test_no_organization_preserves_global_queryset_behavior(self):
        request = RequestFactory().get("/", {"sort": "district"})
        request.organization = None
        school_view = SchoolListView()
        school_view.request = request
        schools = list(school_view.get_queryset())
        self.assertEqual(len(schools), 5)
        self.assertEqual(
            next(s for s in schools if s.pk == self.cross_linked.pk).district,
            self.other_district,
        )
        district_view = DistrictListView()
        district_view.request = request
        districts = {d.pk: d for d in district_view.get_queryset()}
        self.assertEqual(set(districts), {self.district.pk, self.other_district.pk})
        self.assertEqual(districts[self.district.pk].school_count, 2)

    def test_sorting_by_district_does_not_depend_on_foreign_names(self):
        other = SchoolDistrict.objects.create(
            organization=self.other_org, name="Another Foreign District"
        )
        second = School.objects.create(
            organization=self.org, name="Second Cross-linked School", district=other
        )
        url = reverse("school_list")
        first = self.client.get(url, {"sort": "district"})
        ids = [
            s.pk
            for s in first.context["schools"]
            if s.pk in (self.cross_linked.pk, second.pk)
        ]
        self.other_district.name = "A Foreign District"
        self.other_district.save(update_fields=["name"])
        second_response = self.client.get(url, {"sort": "district"})
        self.assertEqual(
            ids,
            [
                s.pk
                for s in second_response.context["schools"]
                if s.pk in (self.cross_linked.pk, second.pk)
            ],
        )
        self.assertNotContains(second_response, "A Foreign District")

    def test_linked_edit_and_delete_pages_reject_foreign_objects(self):
        for name, local, foreign in (
            ("school_edit", self.local_school, self.other_school),
            ("school_district_edit", self.district, self.other_district),
            ("school_district_delete", self.district, self.other_district),
        ):
            with self.subTest(view=name):
                self.assertEqual(
                    self.client.get(reverse(name, args=[local.pk])).status_code, 200
                )
                foreign_url = reverse(name, args=[foreign.pk])
                response = self.client.get(foreign_url)
                self.assertRedirects(
                    response, reverse("home"), fetch_redirect_response=False
                )
                response = self.client.post(foreign_url, {"name": "Changed"})
                self.assertRedirects(
                    response, reverse("home"), fetch_redirect_response=False
                )
        self.other_school.refresh_from_db()
        self.assertEqual(self.other_school.district_id, self.district.pk)
        self.assertEqual(self.other_school.name, "Foreign School")
        self.other_district.refresh_from_db()
        self.assertEqual(self.other_district.name, "Foreign District")
        self.assertTrue(
            SchoolDistrict.objects.filter(pk=self.other_district.pk).exists()
        )

    def test_delete_confirmation_hides_foreign_schools_and_delete_preserves_them(self):
        url = reverse("school_district_delete", args=[self.district.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Local School")
        self.assertNotContains(response, "Foreign School")
        response = self.client.post(url, follow=True)
        self.assertContains(response, "This district cannot be deleted")
        self.assertTrue(SchoolDistrict.objects.filter(pk=self.district.pk).exists())
        self.other_school.refresh_from_db()
        self.local_school.refresh_from_db()
        self.assertEqual(self.other_school.district_id, self.district.pk)
        self.assertEqual(self.local_school.district_id, self.district.pk)
        self.assertFalse(
            AuditLog.objects.filter(
                event=AuditEvent.RECORD_DELETED,
                resource_type="SchoolDistrict",
                resource_id=str(self.district.pk),
            ).exists()
        )

    def test_allowed_delete_records_the_audit_snapshot(self):
        self.other_school.district = None
        self.other_school.save(update_fields=["district"])
        response = self.client.post(
            reverse("school_district_delete", args=[self.district.pk])
        )
        self.assertRedirects(
            response, reverse("school_district_list"), fetch_redirect_response=False
        )
        entry = AuditLog.objects.get(
            event=AuditEvent.RECORD_DELETED,
            resource_type="SchoolDistrict",
            resource_id=str(self.district.pk),
        )
        self.assertEqual(entry.before, {"name": "Local District", "school_count": 1})
        self.local_school.refresh_from_db()
        self.assertIsNone(self.local_school.district_id)

    def test_allowed_delete_survives_audit_database_failure(self):
        self.other_school.district = None
        self.other_school.save(update_fields=["district"])

        def fail_audit_save(*args, **kwargs):
            SchoolDistrict.objects.create(
                organization=self.org, name=self.district.name
            )

        with patch.object(AuditLog, "save", side_effect=fail_audit_save):
            with self.assertLogs("audit", level="ERROR"):
                response = self.client.post(
                    reverse("school_district_delete", args=[self.district.pk])
                )
        self.assertRedirects(
            response, reverse("school_district_list"), fetch_redirect_response=False
        )
        self.assertFalse(SchoolDistrict.objects.filter(pk=self.district.pk).exists())
        self.local_school.refresh_from_db()
        self.assertIsNone(self.local_school.district_id)

    def test_existing_authentication_and_permissions_remain_in_force(self):
        urls = (
            reverse("school_list"),
            reverse("school_district_list"),
            reverse("school_create"),
            reverse("school_district_create"),
            reverse("school_merge"),
            reverse("school_edit", args=[self.local_school.pk]),
            reverse("school_district_edit", args=[self.district.pk]),
            reverse("school_district_delete", args=[self.district.pk]),
        )
        self.client.logout()
        for url in urls:
            with self.subTest(url=url, user="anonymous"):
                self.assertEqual(self.client.get(url).status_code, 302)
        unprivileged = User.objects.create_user(username="unprivileged")
        UserOrganizationMembership.objects.create(
            user=unprivileged, organization=self.org, role="Student"
        )
        self.client.force_login(unprivileged)
        for url in urls[:2]:
            self.assertEqual(self.client.get(url).status_code, 200)
        for url in urls[2:]:
            with self.subTest(url=url, user="unprivileged"):
                self.assertIn(self.client.get(url).status_code, (302, 403))
                self.assertIn(self.client.post(url, {}).status_code, (302, 403))
        response = self.client.post(
            reverse("school_district_create"), {"name": "Unauthorized District"}
        )
        self.assertIn(response.status_code, (302, 403))
        self.assertFalse(
            SchoolDistrict.objects.filter(name="Unauthorized District").exists()
        )


class ImportDashboardViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="lead_mentor",
            email="lead@example.com",
            password="password123",  # nosec B106
        )
        UserOrganizationMembership.objects.create(
            user=self.user,
            organization=Organization.objects.get(slug="gos"),
            role="LeadMentor",
        )
        lead_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.user.groups.add(lead_group)
        for codename in (
            "add_student",
            "add_adult",
            "change_student",
            "add_school",
        ):
            perm = Permission.objects.filter(codename=codename).first()
            if perm:
                self.user.user_permissions.add(perm)

        self.client = Client()
        self.client.force_login(self.user)

        self.program = Program.objects.create(name="Girls of Steel FRC 2026")
        feat, _ = ProgramFeature.objects.get_or_create(key="attendance")
        self.program.features.add(feat)

    def test_import_dashboard_renders(self):
        response = self.client.get(reverse("import_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "imports/dashboard.html")
        self.assertContains(response, "Data Imports")
        self.assertContains(response, "Students")
        self.assertContains(response, "Parents")
        self.assertContains(response, "Student-Parent Relationships")
        self.assertContains(response, "Mentors")
        self.assertContains(response, "Schools")
        self.assertContains(response, "Attendance")
        self.assertContains(response, reverse("students_sample_csv"))
        self.assertContains(response, reverse("parents_sample_csv"))
        self.assertContains(response, reverse("relationships_sample_csv"))
        self.assertContains(response, reverse("mentors_sample_csv"))
        self.assertContains(response, reverse("schools_sample_csv"))
        self.assertContains(response, reverse("attendance_sample_csv"))
