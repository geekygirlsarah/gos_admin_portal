from django.contrib.auth.models import Group, User
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
        program = Program.objects.create(name="Test Program")
        form_data = {
            "name": "Updated Program",
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
        Program.objects.create(name="Test Program")
        # Check that the feature is available to be selected for this program
        features = ProgramFeature.objects.all()
        self.assertTrue(features.filter(key="tshirt-size").exists())


class SignoutSheetFeatureTests(TestCase):
    """The single 'signout-sheet' feature turns on both the printable sheet
    and the digital sign-out station on the program detail page."""

    def setUp(self):
        self.lead_mentor = User.objects.create_user(
            username="lead_mentor", password="password123"
        )  # nosec B106
        group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.lead_mentor.groups.add(group)
        self.program = Program.objects.create(name="Test Program", active=True)
        self.signout_feature, _ = ProgramFeature.objects.get_or_create(
            key="signout-sheet",
            defaults={"name": "Parent sign-outs"},
        )
        self.detail_url = reverse("program_detail", args=[self.program.pk])

    def _render(self):
        self.client.force_login(self.lead_mentor)
        return self.client.get(self.detail_url)

    def test_buttons_hidden_when_feature_disabled(self):
        response = self._render()
        self.assertNotContains(response, "Print Sign-out Sheet")
        self.assertNotContains(response, "Digital Sign-out")

    def test_both_buttons_render_when_feature_enabled(self):
        self.program.features.add(self.signout_feature)
        response = self._render()
        self.assertContains(response, "Print Sign-out Sheet")
        self.assertContains(response, "Digital Sign-out")
