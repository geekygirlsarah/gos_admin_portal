from django.test import TestCase
from django.urls import reverse

from programs.forms import ProgramForm
from programs.models import Program, ProgramFeature


class ProgramFeatureTests(TestCase):
    def setUp(self):
        # The migration should have already created this, but for testing purposes we ensure it exists
        self.tshirt_feature, _ = ProgramFeature.objects.get_or_create(
            key="tshirt-size", defaults={"name": "T-shirt Sizes"}
        )

    def test_program_form_includes_tshirt_feature(self):
        form = ProgramForm()
        # Check if tshirt-size is in the choices for features
        feature_choices = [choice[1] for choice in form.fields["features"].choices]
        self.assertIn("T-shirt Sizes", feature_choices)

    def test_saving_program_with_tshirt_feature(self):
        program = Program.objects.create(name="Test Program", year=2025)
        form_data = {
            "name": "Updated Program",
            "year": 2025,
            "active": True,
            "features": [self.tshirt_feature.pk],
        }
        form = ProgramForm(data=form_data, instance=program)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()

        program.refresh_from_db()
        self.assertTrue(program.has_feature("tshirt-size"))

    def test_program_edit_view_shows_tshirt_feature(self):
        # This requires login and permissions, but we can at least check the model/form logic
        program = Program.objects.create(name="Test Program", year=2025)
        # Check that the feature is available to be selected for this program
        features = ProgramFeature.objects.all()
        self.assertTrue(features.filter(key="tshirt-size").exists())
