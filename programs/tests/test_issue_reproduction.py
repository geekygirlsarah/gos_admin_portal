from django.contrib.auth import get_user_model
from django.test import TestCase

from programs.models import Adult, Student

User = get_user_model()

from django.contrib.auth.models import Group

from programs.forms import StudentForm


class StudentFormProtectionTestCase(TestCase):
    def setUp(self):
        self.lead_mentor_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.lead_user = User.objects.create_user(
            username="lead", password="password"  # nosec
        )
        self.lead_user.groups.add(self.lead_mentor_group)

        self.mentor_user = User.objects.create_user(
            username="mentor", password="password"  # nosec
        )
        # Mentors are NOT LeadMentors by default in this test

        self.student_user = User.objects.create_user(
            username="student", password="password"  # nosec
        )
        self.student = Student.objects.create(
            user=self.student_user, legal_first_name="Legal", last_name="Student"
        )

    def test_lead_mentor_can_see_user_field(self):
        """Lead Mentors should still see the user field in the form."""
        form = StudentForm(instance=self.student, user=self.lead_user)
        self.assertIn("user", form.fields)

    def test_regular_mentor_cannot_see_user_field(self):
        """Regular Mentors should NOT see the user field, preventing accidental disconnection."""
        form = StudentForm(instance=self.student, user=self.mentor_user)
        self.assertNotIn("user", form.fields)

    def test_form_save_without_user_field_preserves_user(self):
        """If user field is missing from form, saving should NOT clear the existing user."""
        form_data = {
            "legal_first_name": "Legal",
            "last_name": "UpdatedStudent",
            "date_of_birth": "2010-01-01",
            "parents": [],
            # 'user' is NOT in form_data
        }
        form = StudentForm(data=form_data, instance=self.student, user=self.mentor_user)
        self.assertTrue(form.is_valid(), form.errors)
        saved_student = form.save()

        self.assertEqual(saved_student.last_name, "UpdatedStudent")
        self.assertEqual(saved_student.user, self.student_user)  # Still connected

    def test_form_init_without_user_does_not_crash_admin(self):
        """
        Django Admin expects all fields in fieldsets to be present in the form.
        If we delete 'user' when no user is provided, the admin crashes with KeyError.
        """
        # This is what Django Admin does: it initializes the form without our custom 'user' kwarg
        form = StudentForm(instance=self.student)

        # Django Admin then tries to access fields. This raised KeyError before the fix.
        try:
            _ = form["user"]
        except KeyError:
            self.fail(
                "StudentForm['user'] raised KeyError when initialized without a user. This crashes the Django Admin."
            )


class NameSyncTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="teststudent",
            email="student@example.com",
            password="password",  # nosec
            first_name="UserFirst",
            last_name="UserLast",
        )
        self.student = Student.objects.create(
            user=self.user,
            legal_first_name="LegalFirst",
            first_name="StudentFirst",
            last_name="StudentLast",
        )

        self.adult_user = User.objects.create_user(
            username="testadult",
            email="adult@example.com",
            password="password",  # nosec
            first_name="AdultUserFirst",
            last_name="AdultUserLast",
        )
        self.adult = Adult.objects.create(
            user=self.adult_user, first_name="AdultFirst", last_name="AdultLast"
        )

    def test_student_name_sync(self):
        """Test that updating Student name DOES update User name now."""
        self.student.first_name = "NewStudentFirst"
        self.student.save()

        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, self.student.first_name)
        self.assertEqual(self.user.last_name, self.student.last_name)

    def test_adult_name_sync(self):
        """Test that updating Adult name DOES update User name now."""
        self.adult.first_name = "NewAdultFirst"
        self.adult.save()

        self.adult_user.refresh_from_db()
        self.assertEqual(self.adult_user.first_name, self.adult.first_name)
        self.assertEqual(self.adult_user.last_name, self.adult.last_name)

    def test_adult_preferred_name_sync(self):
        """Test that Adult preferred name is synced to User first_name."""
        self.adult.preferred_first_name = "Nickname"
        self.adult.save()

        self.adult_user.refresh_from_db()
        self.assertEqual(self.adult_user.first_name, "Nickname")

    def test_user_name_authoritative_issue_resolved(self):
        """Demonstrate that templates using user.first_name now show updated info."""
        self.student.first_name = "UpdatedName"
        self.student.save()

        # Now they are in sync
        self.assertEqual(self.user.first_name, "UpdatedName")
        self.assertEqual(self.student.first_name, "UpdatedName")
