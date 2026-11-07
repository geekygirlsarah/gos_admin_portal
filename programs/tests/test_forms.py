import datetime

from django import forms
from django.test import TestCase

from organizations.models import Organization
from programs.forms import AdultForm, StudentForm
from programs.models import Adult


class StudentFormTests(TestCase):
    def setUp(self):
        self.parent1 = Adult.objects.create(
            legal_first_name="Alex", last_name="Parent", is_parent=True
        )
        self.parent2 = Adult.objects.create(
            legal_first_name="Sage", last_name="Guardian", is_parent=True
        )

    def test_primary_and_secondary_must_differ(self):
        form = StudentForm(
            data={
                "legal_first_name": "Taylor",
                "last_name": "Doe",
                "primary_contact": self.parent1.id,
                "secondary_contact": self.parent1.id,
            },
            organization=self.parent1.organization,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("secondary_contact", form.errors)
        self.assertIn(
            "Secondary contact must be different from Primary contact.",
            form.errors["secondary_contact"],
        )

    def test_grade_selector_sets_graduation_year(self):
        # Grade 12 should set graduation_year to current end-of-year (June/July boundary logic)
        today = datetime.date.today()
        end_year = today.year + (1 if today.month >= 7 else 0)
        form = StudentForm(
            data={
                "legal_first_name": "Jamie",
                "last_name": "Lee",
                "grade_selector": "12",
                "date_of_birth": "2010-01-01",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        student = form.save()
        self.assertEqual(student.graduation_year, end_year)

    def test_parents_sync_includes_primary_and_secondary(self):
        form = StudentForm(
            data={
                "legal_first_name": "Robin",
                "last_name": "Quinn",
                "primary_contact": self.parent1.id,
                "secondary_contact": self.parent2.id,
                "parents": [self.parent1.id],  # only p1 preselected
                "date_of_birth": "2010-01-01",
            },
            organization=self.parent1.organization,
        )
        self.assertTrue(form.is_valid(), form.errors)
        student = form.save()
        # adults should include both parents including secondary
        adult_ids = set(student.adults.values_list("id", flat=True))
        self.assertSetEqual(adult_ids, {self.parent1.id, self.parent2.id})

    def test_state_field_is_dropdown(self):
        form = StudentForm()
        self.assertIsInstance(form.fields["state"].widget, forms.Select)

    def test_state_field_default_is_PA(self):
        form = StudentForm()
        # StudentForm is a ModelForm, it should pick up the default from the model field
        # but let's see if it's in the initial attribute.
        # Actually ModelForm fields have `initial` attribute based on model's default.
        self.assertEqual(form.fields["state"].initial, "PA")

    def test_portal_form_never_exposes_user_field_for_privileged_user(self):
        from django.contrib.auth.models import Group, User

        from programs.models import Student

        lead = User.objects.create_user(username="leadform", password="x")  # nosec B106
        g, _ = Group.objects.get_or_create(name="LeadMentor")
        lead.groups.add(g)
        student = Student.objects.create(legal_first_name="A", last_name="B")
        form = StudentForm(
            data={
                "legal_first_name": "A",
                "last_name": "B",
                "date_of_birth": "2012-01-01",
            },
            instance=student,
            user=lead,
        )
        self.assertNotIn("user", form.fields)

    def test_save_preserves_linked_user_when_user_field_not_submitted(self):
        from django.contrib.auth.models import Group, User

        from programs.models import Student

        lead = User.objects.create_user(username="lead2", password="x")  # nosec B106
        g, _ = Group.objects.get_or_create(name="LeadMentor")
        lead.groups.add(g)
        linked_user = User.objects.create_user(
            username="linked2", password="x"
        )  # nosec B106
        student = Student.objects.create(
            legal_first_name="Alex", last_name="Morgan", user=linked_user
        )
        data = {
            "legal_first_name": "Alex",
            "last_name": "Morgan",
            "date_of_birth": "2012-05-05",
        }
        form = StudentForm(data=data, instance=student, user=lead)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        student.refresh_from_db()
        self.assertEqual(student.user_id, linked_user.pk)

    def test_student_editing_profile_cannot_modify_or_remove_parents(self):
        from django.contrib.auth.models import Group, User

        from programs.models import AdultStudentRelationship, RolePermission, Student

        RolePermission.objects.update_or_create(
            role="Student",
            section="parents_emergency",
            defaults={"can_read": True, "can_write": False},
        )
        student_user = User.objects.create_user(
            username="student1", password="x"  # nosec B106
        )
        group, _ = Group.objects.get_or_create(name="Student")
        student_user.groups.add(group)

        student = Student.objects.create(
            legal_first_name="Alex",
            last_name="Morgan",
            user=student_user,
            primary_contact=self.parent1,
            secondary_contact=self.parent2,
        )

        form = StudentForm(instance=student, user=student_user)
        self.assertNotIn("parents", form.fields)
        self.assertNotIn("primary_contact", form.fields)
        self.assertNotIn("secondary_contact", form.fields)

        # Even if submitted data attempts to clear contacts/parents, save() preserves them
        post_data = {
            "legal_first_name": "Alex",
            "preferred_first_name": "Lex",
            "last_name": "Morgan",
            "date_of_birth": "2012-05-05",
            "parents": [],
            "primary_contact": "",
            "secondary_contact": "",
        }
        bound_form = StudentForm(data=post_data, instance=student, user=student_user)
        self.assertTrue(bound_form.is_valid(), bound_form.errors)
        bound_form.save()

        student.refresh_from_db()
        self.assertEqual(student.preferred_first_name, "Lex")
        self.assertEqual(student.primary_contact, self.parent1)
        self.assertEqual(student.secondary_contact, self.parent2)
        adult_ids = set(student.adults.values_list("id", flat=True))
        self.assertSetEqual(adult_ids, {self.parent1.id, self.parent2.id})


class AdultFormAndrewIdSponsorTests(TestCase):
    """Tests for AdultForm.andrew_id_sponsor queryset filtering and ordering.

    Integrated from test_issue_reproduction.py - verifies sponsor dropdown
    only shows mentors and sorts by preferred_first_name/first_name then last_name.
    """

    def setUp(self):
        self.mentor1 = Adult.objects.create(
            legal_first_name="Zebra", last_name="Alpha", is_mentor=True
        )
        self.mentor2 = Adult.objects.create(
            legal_first_name="Alice",
            preferred_first_name="Betty",
            last_name="Gamma",
            is_mentor=True,
        )
        self.mentor3 = Adult.objects.create(
            legal_first_name="Charlie", last_name="Delta", is_mentor=True
        )
        self.parent = Adult.objects.create(
            legal_first_name="Parent", last_name="User", is_parent=True, is_mentor=False
        )
        self.alumni = Adult.objects.create(
            legal_first_name="Alumni", last_name="User", is_alumni=True, is_mentor=False
        )

    def test_andrew_id_sponsor_queryset_filters_mentors_only(self):
        form = AdultForm(organization=self.mentor1.organization)
        sponsor_field = form.fields.get("org_email_sponsor") or form.fields.get(
            "andrew_id_sponsor"
        )
        queryset = sponsor_field.queryset
        queryset_ids = list(queryset.values_list("id", flat=True))

        for m_id in [self.mentor1.id, self.mentor2.id, self.mentor3.id]:
            self.assertIn(m_id, queryset_ids, f"Mentor {m_id} should be in queryset")

        self.assertNotIn(
            self.parent.id, queryset_ids, "Parent should not be in queryset"
        )
        self.assertNotIn(
            self.alumni.id, queryset_ids, "Alumni should not be in queryset"
        )

    def test_andrew_id_sponsor_queryset_sorted_by_preferred_first_name(self):
        form = AdultForm(organization=self.mentor1.organization)
        sponsor_field = form.fields.get("org_email_sponsor") or form.fields.get(
            "andrew_id_sponsor"
        )
        queryset = sponsor_field.queryset

        # Expected order: Betty Gamma (Alice->Betty), Charlie Delta, Zebra Alpha
        expected_order = [self.mentor2.id, self.mentor3.id, self.mentor1.id]
        actual_order = list(queryset.values_list("id", flat=True))

        self.assertEqual(
            actual_order,
            expected_order,
            f"Expected order {expected_order}, got {actual_order}",
        )

    def test_sponsor_choices_reject_other_organization(self):
        other_organization = Organization.objects.create(
            name="Other Organization", slug="other-organization", subdomain="other"
        )
        foreign_mentor = Adult.objects.create(
            organization=other_organization,
            legal_first_name="Foreign",
            last_name="Mentor",
            is_mentor=True,
        )

        for form_class in (StudentForm, AdultForm):
            with self.subTest(form=form_class.__name__):
                form = form_class(organization=self.mentor1.organization)
                choices = form.fields["org_email_sponsor"].queryset
                self.assertIn(self.mentor1, choices)
                self.assertNotIn(foreign_mentor, choices)

                bound_form = form_class(
                    data={
                        "legal_first_name": "Test",
                        "last_name": "Person",
                        "date_of_birth": "2010-01-01",
                        "org_email_sponsor": foreign_mentor.pk,
                    },
                    organization=self.mentor1.organization,
                )
                self.assertFalse(bound_form.is_valid())
                self.assertIn("org_email_sponsor", bound_form.errors)
