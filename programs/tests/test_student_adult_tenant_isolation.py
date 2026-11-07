from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from organizations.models import Organization, UserOrganizationMembership
from programs.forms import AdultForm, BackgroundChecksForm, StudentForm
from programs.models import (
    Adult,
    AdultStudentRelationship,
    BackgroundCheck,
    BackgroundCheckType,
    School,
    Student,
)
from programs.permission_views import can_user_delete, can_user_read, can_user_write


class StudentAdultTenantIsolationTests(TestCase):
    def setUp(self):
        self.alpha = Organization.objects.create(
            name="Profile Alpha",
            slug="profile-alpha",
            subdomain="profile-alpha",
        )
        self.beta = Organization.objects.create(
            name="Profile Beta",
            slug="profile-beta",
            subdomain="profile-beta",
        )
        self.user = get_user_model().objects.create_user(
            username="profile-alpha-lead",
            email="profile-alpha-lead@example.test",
        )
        UserOrganizationMembership.objects.create(
            user=self.user,
            organization=self.alpha,
            role="LeadMentor",
            is_active=True,
        )
        self.client.force_login(self.user)
        self.host = "profile-alpha.localhost"
        self.local_student = Student.objects.create(
            organization=self.alpha,
            legal_first_name="Local",
            last_name="Student",
            date_of_birth="2008-05-15",
        )
        self.foreign_student = Student.objects.create(
            organization=self.beta,
            legal_first_name="Private Beta",
            last_name="Student",
            date_of_birth="2008-05-15",
        )
        self.local_adult = Adult.objects.create(
            organization=self.alpha,
            legal_first_name="Local",
            last_name="Adult",
            is_parent=True,
        )
        self.foreign_adult = Adult.objects.create(
            organization=self.beta,
            legal_first_name="Private Beta",
            last_name="Adult",
            is_parent=True,
            phone_number="412-555-0999",
        )

    def get_tenant(self, name, pk):
        return self.client.get(
            reverse(name, args=[pk]),
            HTTP_HOST=self.host,
        )

    def post_tenant(self, name, pk, data):
        return self.client.post(
            reverse(name, args=[pk]),
            data,
            HTTP_HOST=self.host,
        )

    def test_foreign_student_detail_and_edit_are_not_accessible(self):
        for route in ("student_detail", "student_edit"):
            with self.subTest(route=route):
                response = self.get_tenant(route, self.foreign_student.pk)
                self.assertNotEqual(response.status_code, 200)
                self.assertNotIn(b"Private Beta", response.content)

    def test_foreign_adult_detail_and_edit_are_not_accessible(self):
        for route in ("adult_detail", "adult_edit"):
            with self.subTest(route=route):
                response = self.get_tenant(route, self.foreign_adult.pk)
                self.assertNotEqual(response.status_code, 200)
                self.assertNotIn(b"Private Beta", response.content)

    def test_same_organization_student_and_adult_details_remain_accessible(self):
        for route, record in (
            ("student_detail", self.local_student),
            ("adult_detail", self.local_adult),
        ):
            with self.subTest(route=route):
                response = self.get_tenant(route, record.pk)
                self.assertEqual(response.status_code, 200)

    def test_permission_helpers_reject_object_from_another_organization(self):
        self.assertFalse(
            can_user_read(
                self.user, "student_info", self.foreign_student, org=self.alpha
            )
        )
        self.assertFalse(
            can_user_write(
                self.user, "student_info", self.foreign_student, org=self.alpha
            )
        )
        self.assertFalse(
            can_user_delete(
                self.user, "student_info", self.foreign_student, org=self.alpha
            )
        )

        plain_user = get_user_model().objects.create_user(
            username="profile-no-role",
            email="profile-no-role@example.test",
        )
        self.assertFalse(
            can_user_read(
                plain_user, "student_info", self.local_student, org=self.alpha
            )
        )

    def test_profile_queries_fail_closed_without_organization(self):
        from programs.views.adults import AdultDetailView, AdultUpdateView
        from programs.views.students import StudentDetailView, StudentUpdateView

        for view_class in (
            AdultDetailView,
            AdultUpdateView,
            StudentDetailView,
            StudentUpdateView,
        ):
            with self.subTest(view=view_class.__name__):
                view = view_class()
                view.request = type("Request", (), {"organization": None})()
                self.assertFalse(view.get_queryset().exists())

    def test_student_detail_omits_foreign_adult_relationship(self):
        AdultStudentRelationship.objects.create(
            organization=self.beta,
            adult=self.foreign_adult,
            student=self.local_student,
            relationship_to_student="parent",
        )

        response = self.get_tenant("student_detail", self.local_student.pk)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Private Beta Adult")

    def test_adult_detail_omits_foreign_student_relationship(self):
        AdultStudentRelationship.objects.create(
            organization=self.beta,
            adult=self.local_adult,
            student=self.foreign_student,
            relationship_to_student="parent",
        )

        response = self.get_tenant("adult_detail", self.local_adult.pk)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Private Beta Student")

    def test_student_edit_ignores_foreign_adult_relationship_id(self):
        response = self.post_tenant(
            "student_edit",
            self.local_student.pk,
            {
                "legal_first_name": "Local",
                "last_name": "Student",
                "date_of_birth": "2008-05-15",
                f"parent_rel_{self.foreign_adult.pk}": "parent",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            AdultStudentRelationship.objects.filter(
                student=self.local_student,
                adult=self.foreign_adult,
            ).exists()
        )

    def test_adult_edit_ignores_foreign_student_relationship_id(self):
        response = self.post_tenant(
            "adult_edit",
            self.local_adult.pk,
            {
                "legal_first_name": "Local",
                "last_name": "Adult",
                f"student_rel_{self.foreign_student.pk}": "parent",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            AdultStudentRelationship.objects.filter(
                adult=self.local_adult,
                student=self.foreign_student,
            ).exists()
        )

    def test_parent_directory_hides_foreign_student_relationship(self):
        AdultStudentRelationship.objects.create(
            organization=self.alpha,
            adult=self.local_adult,
            student=self.foreign_student,
            relationship_to_student="parent",
        )

        response = self.client.get(reverse("parent_list"), HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Private Beta Student")

    def test_emergency_contacts_hide_foreign_adult_relationship(self):
        relationship = AdultStudentRelationship.objects.create(
            organization=self.beta,
            adult=self.foreign_adult,
            student=self.local_student,
            relationship_to_student="parent",
        )
        self.local_student.primary_contact_relationship = relationship
        self.local_student.save(update_fields=["primary_contact_relationship"])

        response = self.client.get(
            reverse("student_emergency_contacts"), HTTP_HOST=self.host
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Private Beta Adult")
        self.assertNotContains(response, "412-555-0999")

    def _create_mentor_with_foreign_student_relationship(self):
        mentor_user = get_user_model().objects.create_user(
            username="profile-alpha-mentor", password="password"
        )  # nosec B106
        UserOrganizationMembership.objects.create(
            user=mentor_user,
            organization=self.alpha,
            role="Mentor",
            is_active=True,
        )
        mentor = Adult.objects.create(
            organization=self.alpha,
            user=mentor_user,
            legal_first_name="Alpha",
            last_name="Mentor",
            is_mentor=True,
        )
        relationship = AdultStudentRelationship.objects.create(
            organization=self.beta,
            adult=mentor,
            student=self.foreign_student,
            relationship_to_student="grandparent",
        )
        return mentor_user, mentor, relationship

    def test_mentor_edit_hides_foreign_student_relationship(self):
        mentor_user, mentor, _ = self._create_mentor_with_foreign_student_relationship()
        self.client.force_login(mentor_user)

        response = self.client.get(
            reverse("mentor_edit", args=[mentor.pk]), HTTP_HOST=self.host
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Private Beta Student")

    def test_mentor_edit_does_not_update_foreign_relationship(self):
        mentor_user, mentor, relationship = (
            self._create_mentor_with_foreign_student_relationship()
        )
        self.client.force_login(mentor_user)

        response = self.client.post(
            reverse("mentor_edit", args=[mentor.pk]),
            {
                "legal_first_name": "Alpha",
                "last_name": "Mentor",
                f"student_rel_{self.foreign_student.pk}": "sibling",
            },
            HTTP_HOST=self.host,
        )

        self.assertEqual(response.status_code, 302)
        relationship.refresh_from_db()
        self.assertEqual(relationship.organization, self.beta)
        self.assertEqual(relationship.relationship_to_student, "grandparent")

    def test_parent_directory_hides_foreign_student_relationship(self):
        AdultStudentRelationship.objects.create(
            organization=self.alpha,
            adult=self.local_adult,
            student=self.foreign_student,
            relationship_to_student="parent",
        )

        response = self.client.get(reverse("parent_list"), HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Private Beta Student")

    def test_emergency_contacts_hide_foreign_adult_relationship(self):
        relationship = AdultStudentRelationship.objects.create(
            organization=self.beta,
            adult=self.foreign_adult,
            student=self.local_student,
            relationship_to_student="parent",
        )
        self.local_student.primary_contact_relationship = relationship
        self.local_student.save(update_fields=["primary_contact_relationship"])

        response = self.client.get(
            reverse("student_emergency_contacts"), HTTP_HOST=self.host
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Private Beta Adult")
        self.assertNotContains(response, "412-555-0999")

    def create_mentor_with_foreign_student_relationship(self):
        mentor_user = get_user_model().objects.create_user(
            username="profile-alpha-mentor", password="password"
        )  # nosec B106
        UserOrganizationMembership.objects.create(
            user=mentor_user,
            organization=self.alpha,
            role="Mentor",
            is_active=True,
        )
        mentor = Adult.objects.create(
            organization=self.alpha,
            user=mentor_user,
            legal_first_name="Alpha",
            last_name="Mentor",
            is_mentor=True,
        )
        relationship = AdultStudentRelationship.objects.create(
            organization=self.beta,
            adult=mentor,
            student=self.foreign_student,
            relationship_to_student="grandparent",
        )
        return mentor_user, mentor, relationship

    def test_mentor_edit_hides_foreign_student_relationship(self):
        mentor_user, mentor, _ = self.create_mentor_with_foreign_student_relationship()
        self.client.force_login(mentor_user)

        response = self.client.get(
            reverse("mentor_edit", args=[mentor.pk]), HTTP_HOST=self.host
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Private Beta Student")

    def test_mentor_edit_does_not_update_foreign_relationship(self):
        mentor_user, mentor, relationship = (
            self.create_mentor_with_foreign_student_relationship()
        )
        self.client.force_login(mentor_user)

        response = self.client.post(
            reverse("mentor_edit", args=[mentor.pk]),
            {
                "legal_first_name": "Alpha",
                "last_name": "Mentor",
                f"student_rel_{self.foreign_student.pk}": "sibling",
            },
            HTTP_HOST=self.host,
        )

        self.assertEqual(response.status_code, 302)
        relationship.refresh_from_db()
        self.assertEqual(relationship.organization, self.beta)
        self.assertEqual(relationship.relationship_to_student, "grandparent")

    def test_profile_forms_keep_lead_controls_and_only_offer_local_people(self):
        student_form = StudentForm(user=self.user, organization=self.alpha)
        adult_form = AdultForm(user=self.user, organization=self.alpha)

        self.assertIn("parents", student_form.fields)
        self.assertIn("students", adult_form.fields)
        self.assertEqual(
            list(student_form.fields["parents"].queryset), [self.local_adult]
        )
        self.assertEqual(
            list(adult_form.fields["students"].queryset), [self.local_student]
        )

    def test_student_form_keeps_the_shared_school_catalog(self):
        shared_school = School.objects.create(name="Shared Catalog School")
        student_form = StudentForm(user=self.user, organization=self.alpha)

        self.assertIn(shared_school, student_form.fields["school"].queryset)

    def test_background_check_save_does_not_delete_foreign_owned_child(self):
        check_type = BackgroundCheckType.objects.create(
            organization=self.alpha,
            name="Alpha Check",
            validity_years=5,
        )
        foreign_check = BackgroundCheck.objects.create(
            organization=self.beta,
            student=self.local_student,
            check_type=check_type,
            cleared=True,
        )
        form = BackgroundChecksForm(
            data={},
            organization=self.alpha,
            holder=self.local_student,
        )

        self.assertTrue(form.is_valid(), form.errors)
        form.save(student=self.local_student)

        self.assertTrue(BackgroundCheck.objects.filter(pk=foreign_check.pk).exists())
