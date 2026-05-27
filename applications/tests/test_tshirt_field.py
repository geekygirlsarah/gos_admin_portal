from django.test import TestCase
from applications.forms import StudentInfoForm

class TshirtFieldTests(TestCase):
    def test_tshirt_field_presence_by_default(self):
        # Currently, it should always be present
        form = StudentInfoForm()
        self.assertIn('tshirt_size', form.fields)

    def test_tshirt_field_removable(self):
        # When tshirt_enabled is False, it should be removed
        form = StudentInfoForm(tshirt_enabled=False)
        self.assertNotIn('tshirt_size', form.fields)

    def test_tshirt_field_present_when_enabled(self):
        # When tshirt_enabled is True, it should be present
        form = StudentInfoForm(tshirt_enabled=True)
        self.assertIn('tshirt_size', form.fields)
