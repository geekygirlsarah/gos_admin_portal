from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.test import TestCase

from programs.admin import StudentAdmin
from programs.models import Student


class StudentAdminFormTests(TestCase):
    def test_student_admin_get_form_does_not_reference_unknown_fields(self):
        # Create a minimal student instance (not strictly needed for get_form)
        Student.objects.create(legal_first_name="Alex", last_name="Morgan")

        site = AdminSite()
        admin = StudentAdmin(Student, site)

        # This should not raise FieldError due to non-existent fields
        form_class = admin.get_form(request=None)

        # Sanity check: some known fields are present
        self.assertIn("preferred_first_name", form_class.base_fields)
        self.assertIn("last_name", form_class.base_fields)

    def test_student_admin_form_includes_user_field(self):
        Student.objects.create(legal_first_name="Alex", last_name="Morgan")
        site = AdminSite()
        admin = StudentAdmin(Student, site)
        form_class = admin.get_form(request=None)
        self.assertIn("user", form_class.base_fields)

    def test_student_admin_form_preserves_link_when_user_not_submitted(self):
        """When the admin form is instantiated without submitting the 'user'
        field (e.g. a custom admin flow), the existing link must be kept."""
        User = get_user_model()
        user = User.objects.create_user(username="linked", password="x")  # nosec B106
        student = Student.objects.create(
            legal_first_name="Alex", last_name="Morgan", user=user
        )
        site = AdminSite()
        admin = StudentAdmin(Student, site)
        form_class = admin.get_form(request=None)
        # POST data omits 'user' entirely — simulating an admin flow
        form = form_class(
            data={
                "legal_first_name": "Alex",
                "last_name": "Morgan",
                "date_of_birth": "2012-05-05",
            },
            instance=student,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        student.refresh_from_db()
        self.assertEqual(student.user, user)

    def test_student_admin_form_allows_changing_user_link(self):
        """An admin who deliberately submits a different user should update the link."""
        User = get_user_model()
        user_a = User.objects.create_user(username="a", password="x")  # nosec B106
        user_b = User.objects.create_user(username="b", password="x")  # nosec B106
        student = Student.objects.create(
            legal_first_name="Alex", last_name="Morgan", user=user_a
        )
        site = AdminSite()
        admin = StudentAdmin(Student, site)
        form_class = admin.get_form(request=None)
        form = form_class(
            data={
                "legal_first_name": "Alex",
                "last_name": "Morgan",
                "date_of_birth": "2012-05-05",
                "user": str(user_b.pk),
            },
            instance=student,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        student.refresh_from_db()
        self.assertEqual(student.user, user_b)
