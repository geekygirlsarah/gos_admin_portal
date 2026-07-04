from django.contrib.admin.sites import AdminSite
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
        self.assertIn("first_name", form_class.base_fields)
        self.assertIn("last_name", form_class.base_fields)
