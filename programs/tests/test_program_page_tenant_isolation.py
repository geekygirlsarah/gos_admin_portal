from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.test import TestCase, override_settings
from django.urls import reverse

from organizations.models import Organization, UserOrganizationMembership
from programs.models import Enrollment, Program, Student, Team


@override_settings(
    ALLOWED_HOSTS=["*"],
    GEOCODING_DELAY_SECONDS=0,
    MOMENTUM_ROOT_DOMAIN="localhost",
)
class ProgramPageTenantIsolationTests(TestCase):
    def setUp(self):
        self.alpha = Organization.objects.create(
            name="Program Alpha", slug="program-alpha", subdomain="program-alpha"
        )
        self.beta = Organization.objects.create(
            name="Program Beta", slug="program-beta", subdomain="program-beta"
        )
        self.alpha_program = Program.objects.create(
            organization=self.alpha, name="Local Program", active=True
        )
        self.beta_program = Program.objects.create(
            organization=self.beta, name="Foreign Program", active=True
        )
        self.user = User.objects.create_user(
            username="program-alpha-lead", password="password123"
        )  # nosec B106
        lead_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.user.groups.add(lead_group)
        UserOrganizationMembership.objects.create(
            user=self.user,
            organization=self.alpha,
            role="LeadMentor",
            is_active=True,
        )
        self.client.force_login(self.user)

    def _student(self, organization, first_name):
        return Student.objects.create(
            organization=organization,
            legal_first_name=first_name,
            last_name="Student",
            address=f"{first_name} Main St.",
            city="Pittsburgh",
            state="PA",
            zip_code="15213",
        )

    def test_program_pages_only_show_organization_owned_programs_and_students(self):
        local_student = self._student(self.alpha, "Alpha")
        Enrollment.objects.create(
            organization=self.alpha,
            student=local_student,
            program=self.alpha_program,
            active=True,
        )
        foreign_student = self._student(self.beta, "Beta")
        Enrollment.objects.create(
            organization=self.beta,
            student=foreign_student,
            program=self.beta_program,
            active=True,
        )
        Enrollment.objects.create(
            organization=self.alpha,
            student=foreign_student,
            program=self.alpha_program,
            active=True,
        )

        local_paths = (
            reverse("program_student_photos", args=[self.alpha_program.pk]),
            reverse("program_student_map", args=[self.alpha_program.pk]),
            reverse("program_signout_sheet", args=[self.alpha_program.pk]),
            reverse("program_schools", args=[self.alpha_program.pk]),
        )
        foreign_paths = (
            reverse("program_student_photos", args=[self.beta_program.pk]),
            reverse("program_student_map", args=[self.beta_program.pk]),
            reverse("program_signout_sheet", args=[self.beta_program.pk]),
            reverse("program_schools", args=[self.beta_program.pk]),
        )

        with patch("programs.views.programs.resolve_address_points", return_value={}):
            for path in local_paths:
                with self.subTest(path=path):
                    response = self.client.get(
                        path, HTTP_HOST="program-alpha.localhost"
                    )
                    self.assertEqual(response.status_code, 200)
                    self.assertContains(response, "Alpha")
                    self.assertNotContains(response, "Beta")

            for path in foreign_paths:
                with self.subTest(path=path):
                    response = self.client.get(
                        path, HTTP_HOST="program-alpha.localhost"
                    )
                    self.assertNotEqual(response.status_code, 200)
                    self.assertNotIn(b"Beta", response.content)

    def test_program_detail_team_presence_is_organization_scoped(self):
        Team.objects.create(
            organization=self.beta,
            team_type="FRC",
            number=9999,
        )

        response = self.client.get(
            reverse("program_detail", args=[self.alpha_program.pk]),
            HTTP_HOST="program-alpha.localhost",
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["has_teams_or_subteams"])
