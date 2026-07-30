from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from programs.forms import AdultForm, StudentForm
from programs.models import Adult, Student

User = get_user_model()


class DualListboxRenderingTest(TestCase):
    def setUp(self):
        self.lead_mentor_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.user = User.objects.create_user(
            username="admin", password="password"
        )  # nosec B106
        self.user.groups.add(self.lead_mentor_group)
        self.user.is_staff = True
        self.user.save()

        # Create some students
        self.s1 = Student.objects.create(first_name="Alice", last_name="Alpha")
        self.s2 = Student.objects.create(first_name="Bob", last_name="Bravo")
        self.s3 = Student.objects.create(first_name="Charlie", last_name="Charlie")

    def test_adult_form_students_field_rendering(self):
        """
        Test that the students field in AdultForm is now rendered as a dual listbox.
        """
        form = AdultForm(user=self.user)
        html = form.as_p()

        # Should have the dual-listbox class
        self.assertIn('class="dual-listbox"', html)

        # Should have two select boxes: available and selected
        self.assertIn('class="form-select dual-listbox-available"', html)
        self.assertIn('class="form-select dual-listbox-selected"', html)

        # The selected one should have the name attribute
        self.assertIn('name="students"', html)

        # Buttons should be present
        self.assertIn("dual-listbox-add", html)
        self.assertIn("dual-listbox-remove", html)

        # Check if students are in the options
        self.assertIn(str(self.s1), html)
        self.assertIn(str(self.s2), html)
        self.assertIn(str(self.s3), html)

    def test_student_form_parents_field_rendering(self):
        """
        Test that the parents field in StudentForm is now rendered as a dual listbox.
        """
        form = StudentForm(user=self.user)
        html = form.as_p()

        # Should have the dual-listbox class
        self.assertIn('class="dual-listbox"', html)

        # Should have the name attribute
        self.assertIn('name="parents"', html)

        # Should have the custom labels
        self.assertIn("Available Parents", html)
        self.assertIn("Selected Parents", html)

        # Search box should be present
        self.assertIn("dual-listbox-search-available", html)
        self.assertIn("dual-listbox-search-selected", html)
