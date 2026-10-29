from django.contrib.auth.models import Group, Permission, User
from django.test import Client, TestCase
from django.urls import reverse

from programs.models import Program, ProgramFeature, School, SchoolDistrict


class SchoolViewsTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="lead_mentor",
            email="lead@example.com",
            password="password123",  # nosec B106
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


class ImportDashboardViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="lead_mentor",
            email="lead@example.com",
            password="password123",  # nosec B106
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
