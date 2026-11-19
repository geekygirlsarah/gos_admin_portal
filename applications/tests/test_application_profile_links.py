from django.core.exceptions import ValidationError
from django.test import TestCase

from applications.models import Application
from organizations.models import Organization
from programs.models import Adult, Student


class ApplicationProfileLinkTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(
            name="Application Profiles",
            slug="application-profiles",
            subdomain="application-profiles",
        )
        self.other_organization = Organization.objects.create(
            name="Other Application Profiles",
            slug="other-application-profiles",
            subdomain="other-application-profiles",
        )

    def test_workflow_links_default_to_legacy_and_accept_same_tenant_profiles(self):
        application = Application.objects.create(organization=self.organization)
        student = Student.all_objects.create(
            organization=self.organization,
            legal_first_name="Draft",
            last_name="Student",
            is_incomplete_application_profile=True,
        )
        primary = Adult.all_objects.create(
            organization=self.organization,
            legal_first_name="Primary",
            last_name="Guardian",
            is_incomplete_application_profile=True,
        )
        secondary = Adult.all_objects.create(
            organization=self.organization,
            legal_first_name="Secondary",
            last_name="Guardian",
            is_incomplete_application_profile=True,
        )

        self.assertEqual(application.profile_workflow_version, 0)
        self.assertIsNone(application.student_profile_id)
        application.student_profile = student
        application.primary_guardian_profile = primary
        application.secondary_guardian_profile = secondary
        application.save(
            update_fields=(
                "student_profile",
                "primary_guardian_profile",
                "secondary_guardian_profile",
            )
        )

        application.refresh_from_db()
        self.assertEqual(application.student_profile, student)
        self.assertEqual(application.primary_guardian_profile, primary)
        self.assertEqual(application.secondary_guardian_profile, secondary)

    def test_application_rejects_profile_links_from_another_organization(self):
        foreign_student = Student.all_objects.create(
            organization=self.other_organization,
            legal_first_name="Foreign",
            last_name="Student",
            is_incomplete_application_profile=True,
        )
        application = Application(
            organization=self.organization,
            student_profile=foreign_student,
        )

        with self.assertRaises(ValidationError):
            application.save()
