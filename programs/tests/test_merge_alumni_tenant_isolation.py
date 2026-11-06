from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse

from organizations.models import Organization, UserOrganizationMembership
from programs.forms import ParentMergeForm, StudentMergeForm
from programs.models import (
    Adult,
    AdultStudentRelationship,
    Enrollment,
    Program,
    Student,
)
from programs.utils.alumni import convert_student_to_alumni, find_matching_alumni_adult
from programs.utils.merges import perform_parent_merge, perform_student_merge
from travel.models import (
    TravelEvent,
    TravelMentorSignup,
    TravelParentChaperoneSignup,
    TravelSignup,
)


class MergeAlumniTenantIsolationTests(TestCase):
    def setUp(self):
        self.alpha = Organization.objects.create(
            name="Merge Alpha",
            slug="merge-alpha",
            subdomain="merge-alpha",
        )
        self.beta = Organization.objects.create(
            name="Merge Beta",
            slug="merge-beta",
            subdomain="merge-beta",
        )
        self.alpha_host = "merge-alpha.localhost"
        self.lead = User.objects.create_user(
            username="merge-alpha-lead",
            email="merge-alpha-lead@example.test",
        )
        lead_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.lead.groups.add(lead_group)
        UserOrganizationMembership.objects.create(
            user=self.lead,
            organization=self.alpha,
            role="LeadMentor",
            is_active=True,
        )
        self.client = Client()
        self.client.force_login(self.lead)

        self.superuser = User.objects.create_superuser(
            username="merge-platform-admin",
            email="merge-platform-admin@example.test",
            password=None,
        )
        self.admin_client = Client()
        self.admin_client.force_login(self.superuser)

    def make_student(self, organization, first="Alex", last="Taylor", **kwargs):
        return Student.objects.create(
            organization=organization,
            legal_first_name=first,
            last_name=last,
            **kwargs,
        )

    def make_adult(self, organization, first="Parent", last="Taylor", **kwargs):
        return Adult.objects.create(
            organization=organization,
            legal_first_name=first,
            last_name=last,
            is_parent=True,
            **kwargs,
        )

    def tenant_get(self, path, **kwargs):
        return self.client.get(path, HTTP_HOST=self.alpha_host, **kwargs)

    def tenant_post(self, path, data=None, **kwargs):
        return self.client.post(
            path, data=data or {}, HTTP_HOST=self.alpha_host, **kwargs
        )

    def test_student_merge_form_and_page_only_offer_local_students(self):
        local_student = self.make_student(self.alpha, first="Local")
        foreign_student = self.make_student(self.beta, first="Foreign")

        response = self.tenant_get(reverse("student_merge"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {student.pk for student in response.context["students"]},
            {local_student.pk},
        )
        self.assertEqual(
            set(
                response.context["form"]
                .fields["keep"]
                .queryset.values_list("pk", flat=True)
            ),
            {local_student.pk},
        )

    def test_student_merge_rejects_a_foreign_source_id(self):
        local_student = self.make_student(self.alpha, first="Local")
        foreign_student = self.make_student(self.beta, first="Foreign")

        response = self.tenant_post(
            reverse("student_merge"),
            {"keep": local_student.pk, "source": foreign_student.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(Student.objects.filter(pk=local_student.pk).exists())
        self.assertTrue(Student.objects.filter(pk=foreign_student.pk).exists())

    def test_parent_merge_form_and_page_only_offer_local_adults(self):
        local_adult = self.make_adult(self.alpha, first="Local Parent")
        foreign_adult = self.make_adult(self.beta, first="Foreign Parent")

        response = self.tenant_get(reverse("parent_merge"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {adult.pk for adult in response.context["parents"]}, {local_adult.pk}
        )
        self.assertEqual(
            set(
                response.context["form"]
                .fields["keep"]
                .queryset.values_list("pk", flat=True)
            ),
            {local_adult.pk},
        )

    def test_parent_merge_rejects_a_foreign_source_id(self):
        local_adult = self.make_adult(self.alpha, first="Local Parent")
        foreign_adult = self.make_adult(self.beta, first="Foreign Parent")

        response = self.tenant_post(
            reverse("parent_merge"),
            {"keep": local_adult.pk, "source": foreign_adult.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(Adult.objects.filter(pk=local_adult.pk).exists())
        self.assertTrue(Adult.objects.filter(pk=foreign_adult.pk).exists())

    def test_parent_merge_page_hides_students_from_foreign_owned_relationships(self):
        local_adult = self.make_adult(self.alpha, first="Local Parent")
        local_student = self.make_student(self.alpha, first="Local Student")
        AdultStudentRelationship.objects.create(
            organization=self.beta,
            adult=local_adult,
            student=local_student,
        )

        response = self.tenant_get(reverse("parent_merge"))

        self.assertEqual(response.status_code, 200)
        local_parent = next(
            adult for adult in response.context["parents"] if adult.pk == local_adult.pk
        )
        self.assertEqual(local_parent.all_students(), [])

    def test_merge_forms_have_no_choices_without_organization(self):
        student_form = StudentMergeForm()
        parent_form = ParentMergeForm()

        self.assertFalse(student_form.fields["keep"].queryset.exists())
        self.assertFalse(student_form.fields["source"].queryset.exists())
        self.assertFalse(parent_form.fields["keep"].queryset.exists())
        self.assertFalse(parent_form.fields["source"].queryset.exists())

    def test_student_merge_helper_rejects_cross_organization_records(self):
        keep = self.make_student(self.alpha, first="Keep")
        source = self.make_student(self.beta, first="Source")

        with self.assertRaises(ValidationError):
            perform_student_merge(keep, source)

        self.assertTrue(Student.objects.filter(pk=keep.pk).exists())
        self.assertTrue(Student.objects.filter(pk=source.pk).exists())

    def test_parent_merge_helper_rejects_cross_organization_records(self):
        keep = self.make_adult(self.alpha, first="Keep")
        source = self.make_adult(self.beta, first="Source")

        with self.assertRaises(ValidationError):
            perform_parent_merge(keep, source)

        self.assertTrue(Adult.objects.filter(pk=keep.pk).exists())
        self.assertTrue(Adult.objects.filter(pk=source.pk).exists())

    def test_student_merge_stops_before_transferring_foreign_owned_enrollment(self):
        keep = self.make_student(self.alpha, first="Keep")
        source = self.make_student(self.alpha, first="Source")
        program = Program.objects.create(organization=self.alpha, name="Alpha Program")
        enrollment = Enrollment.objects.create(
            organization=self.beta,
            student=source,
            program=program,
        )

        with self.assertRaises(ValidationError):
            perform_student_merge(keep, source)

        self.assertTrue(Student.objects.filter(pk=source.pk).exists())
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.student_id, source.pk)
        self.assertEqual(enrollment.organization_id, self.beta.pk)

    def test_student_merge_view_reports_inconsistent_enrollment_without_mutation(self):
        keep = self.make_student(self.alpha, first="Keep")
        source = self.make_student(self.alpha, first="Source")
        program = Program.objects.create(organization=self.alpha, name="Alpha Program")
        enrollment = Enrollment.objects.create(
            organization=self.beta,
            student=source,
            program=program,
        )

        response = self.tenant_post(
            reverse("student_merge"), {"keep": keep.pk, "source": source.pk}
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].non_field_errors())
        self.assertTrue(Student.objects.filter(pk=source.pk).exists())
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.student_id, source.pk)

    def test_parent_merge_stops_before_transferring_foreign_owned_relationship(self):
        keep = self.make_adult(self.alpha, first="Keep")
        source = self.make_adult(self.alpha, first="Source")
        student = self.make_student(self.alpha)
        relationship = AdultStudentRelationship.objects.create(
            organization=self.beta,
            adult=source,
            student=student,
        )

        with self.assertRaises(ValidationError):
            perform_parent_merge(keep, source)

        self.assertTrue(Adult.objects.filter(pk=source.pk).exists())
        relationship.refresh_from_db()
        self.assertEqual(relationship.adult_id, source.pk)
        self.assertEqual(relationship.organization_id, self.beta.pk)

    def test_parent_merge_view_reports_inconsistent_relationship_without_mutation(self):
        keep = self.make_adult(self.alpha, first="Keep")
        source = self.make_adult(self.alpha, first="Source")
        student = self.make_student(self.alpha)
        relationship = AdultStudentRelationship.objects.create(
            organization=self.beta,
            adult=source,
            student=student,
        )

        response = self.tenant_post(
            reverse("parent_merge"), {"keep": keep.pk, "source": source.pk}
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].non_field_errors())
        self.assertTrue(Adult.objects.filter(pk=source.pk).exists())
        relationship.refresh_from_db()
        self.assertEqual(relationship.adult_id, source.pk)

    def test_student_merge_stops_before_deleting_foreign_owned_travel_signup(self):
        keep = self.make_student(self.alpha, first="Keep")
        source = self.make_student(self.alpha, first="Source")
        event = TravelEvent.objects.create(
            organization=self.alpha,
            name="Alpha trip",
            start_date="2035-05-01",
            end_date="2035-05-02",
        )
        signup = TravelSignup.objects.create(
            organization=self.beta,
            student=source,
            event=event,
        )

        with self.assertRaises(ValidationError):
            perform_student_merge(keep, source)

        self.assertTrue(Student.objects.filter(pk=source.pk).exists())
        signup.refresh_from_db()
        self.assertEqual(signup.student_id, source.pk)
        self.assertEqual(signup.organization_id, self.beta.pk)

    def test_parent_merge_stops_before_deleting_foreign_owned_travel_signup(self):
        keep = self.make_adult(self.alpha, first="Keep")
        source = self.make_adult(self.alpha, first="Source")
        event = TravelEvent.objects.create(
            organization=self.alpha,
            name="Alpha trip",
            start_date="2035-05-01",
            end_date="2035-05-02",
        )
        signup = TravelMentorSignup.objects.create(
            organization=self.beta,
            adult=source,
            event=event,
        )

        with self.assertRaises(ValidationError):
            perform_parent_merge(keep, source)

        self.assertTrue(Adult.objects.filter(pk=source.pk).exists())
        signup.refresh_from_db()
        self.assertEqual(signup.adult_id, source.pk)
        self.assertEqual(signup.organization_id, self.beta.pk)

    def test_parent_merge_stops_before_deleting_foreign_owned_chaperone_signup(self):
        keep = self.make_adult(self.alpha, first="Keep")
        source = self.make_adult(self.alpha, first="Source")
        event = TravelEvent.objects.create(
            organization=self.alpha,
            name="Alpha trip",
            start_date="2035-05-01",
            end_date="2035-05-02",
        )
        signup = TravelParentChaperoneSignup.objects.create(
            organization=self.beta,
            adult=source,
            event=event,
        )

        with self.assertRaises(ValidationError):
            perform_parent_merge(keep, source)

        self.assertTrue(Adult.objects.filter(pk=source.pk).exists())
        signup.refresh_from_db()
        self.assertEqual(signup.adult_id, source.pk)
        self.assertEqual(signup.organization_id, self.beta.pk)

    def test_alumni_matching_does_not_select_an_adult_from_another_org(self):
        student = self.make_student(
            self.alpha,
            first="Graduating",
            personal_email="graduate@example.test",
        )
        foreign_adult = self.make_adult(
            self.beta,
            first="Graduating",
            personal_email="graduate@example.test",
            is_alumni=True,
        )

        self.assertIsNone(find_matching_alumni_adult(student))

        adult, created, marked_graduated = convert_student_to_alumni(student)

        self.assertTrue(created)
        self.assertTrue(marked_graduated)
        self.assertEqual(adult.organization_id, self.alpha.pk)
        self.assertNotEqual(adult.pk, foreign_adult.pk)
        foreign_adult.refresh_from_db()
        self.assertIsNone(foreign_adult.student_record_id)

    def test_conversion_rejects_existing_foreign_adult_link(self):
        student = self.make_student(self.alpha, first="Graduating")
        foreign_adult = self.make_adult(
            self.beta,
            first="Graduating",
            is_alumni=False,
            student_record=student,
        )

        with self.assertRaises(ValidationError):
            convert_student_to_alumni(student)

        foreign_adult.refresh_from_db()
        student.refresh_from_db()
        self.assertFalse(foreign_adult.is_alumni)
        self.assertFalse(student.graduated)
        self.assertEqual(foreign_adult.student_record_id, student.pk)

    def test_single_alumni_conversion_cannot_target_another_org_student(self):
        foreign_student = self.make_student(self.beta, first="Foreign Graduate")

        self.admin_client.post(
            reverse("student_convert_to_alumni", kwargs={"pk": foreign_student.pk}),
            HTTP_HOST=self.alpha_host,
        )

        foreign_student.refresh_from_db()
        self.assertFalse(foreign_student.graduated)
        self.assertFalse(Adult.objects.filter(student_record=foreign_student).exists())

    def test_bulk_alumni_list_and_post_are_tenant_scoped(self):
        local_student = self.make_student(
            self.alpha, first="Local Graduate", graduation_year=2035
        )
        foreign_student = self.make_student(
            self.beta, first="Foreign Graduate", graduation_year=2035
        )

        response = self.tenant_get(
            f'{reverse("student_bulk_convert_select")}?year=2035'
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Local Graduate")
        self.assertNotContains(response, "Foreign Graduate")
        self.assertEqual(
            {student.pk for student in response.context["students"]},
            {local_student.pk},
        )

        self.tenant_post(
            reverse("student_bulk_convert_select"),
            {
                "action": "convert",
                "student_ids": [str(foreign_student.pk)],
                "year": "2035",
            },
        )

        foreign_student.refresh_from_db()
        self.assertFalse(foreign_student.graduated)
        self.assertFalse(Adult.objects.filter(student_record=foreign_student).exists())
