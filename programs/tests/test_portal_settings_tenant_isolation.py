from django.contrib.auth import get_user_model
from django.http import Http404
from django.test import RequestFactory, TestCase
from django.urls import reverse

from attendance.models import KioskConfig
from organizations.models import Organization, UserOrganizationMembership
from programs.models import Crew, Enrollment, Program, Student, SubTeam, Team
from programs.views.portal_settings import PortalSettingsView, PortalTeamView


class PortalSettingsTenantIsolationTests(TestCase):
    def setUp(self):
        self.alpha = Organization.objects.create(
            name="Settings Alpha",
            slug="settings-alpha",
            subdomain="settings-alpha",
        )
        self.beta = Organization.objects.create(
            name="Settings Beta",
            slug="settings-beta",
            subdomain="settings-beta",
        )
        self.alpha_program = Program.objects.create(
            organization=self.alpha,
            name="Alpha Program",
        )
        self.beta_program = Program.objects.create(
            organization=self.beta,
            name="Beta Program",
        )
        self.user = get_user_model().objects.create_user(
            username="settings-alpha-lead",
            email="settings-alpha-lead@example.test",
        )
        UserOrganizationMembership.objects.create(
            user=self.user,
            organization=self.alpha,
            role="LeadMentor",
            is_active=True,
        )
        self.client.force_login(self.user)
        self.host = "settings-alpha.localhost"
        self.alpha_team = Team.objects.create(
            organization=self.alpha,
            team_type="FRC",
            number=101,
            name="Alpha Team",
        )
        self.beta_team = Team.objects.create(
            organization=self.beta,
            team_type="FRC",
            number=202,
            name="Beta Team",
        )
        self.alpha_crew = Crew.objects.create(
            organization=self.alpha,
            program=self.alpha_program,
            name="Alpha Crew",
        )
        self.beta_crew = Crew.objects.create(
            organization=self.beta,
            program=self.beta_program,
            name="Beta Crew",
        )
        self.alpha_subteam = SubTeam.objects.create(
            organization=self.alpha,
            program=self.alpha_program,
            name="Alpha Subteam",
        )
        self.beta_subteam = SubTeam.objects.create(
            organization=self.beta,
            program=self.beta_program,
            name="Beta Subteam",
        )
        self.alpha_kiosk = KioskConfig.objects.create(
            organization=self.alpha,
            program=self.alpha_program,
            label="Alpha Kiosk",
        )
        self.beta_kiosk = KioskConfig.objects.create(
            organization=self.beta,
            program=self.beta_program,
            label="Beta Kiosk",
        )

    def post(self, route, data):
        return self.client.post(reverse(route), data, HTTP_HOST=self.host)

    def test_settings_page_lists_only_request_organization_records(self):
        response = self.client.get(reverse("portal_settings"), HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["teams"]), [self.alpha_team])
        self.assertEqual(list(response.context["crews"]), [self.alpha_crew])
        self.assertEqual(list(response.context["subteams"]), [self.alpha_subteam])
        self.assertEqual(list(response.context["programs"]), [self.alpha_program])
        self.assertEqual(list(response.context["kiosk_configs"]), [self.alpha_kiosk])

    def test_settings_and_actions_fail_closed_without_organization(self):
        request = RequestFactory().get(reverse("portal_settings"))
        request.user = self.user
        request.organization = None
        with self.assertRaises(Http404):
            PortalSettingsView().get(request)

        post_request = RequestFactory().post(
            reverse("portal_team"),
            {"action": "delete_team", "team_id": self.alpha_team.pk},
        )
        post_request.user = self.user
        post_request.organization = None
        with self.assertRaises(Http404):
            PortalTeamView().post(post_request)

    def test_non_lead_member_cannot_change_settings_records(self):
        mentor = get_user_model().objects.create_user(
            username="settings-alpha-mentor",
            email="settings-alpha-mentor@example.test",
        )
        UserOrganizationMembership.objects.create(
            user=mentor,
            organization=self.alpha,
            role="Mentor",
            is_active=True,
        )
        self.client.force_login(mentor)

        self.post(
            "portal_team",
            {"action": "delete_team", "team_id": self.alpha_team.pk},
        )

        self.assertTrue(Team.objects.filter(pk=self.alpha_team.pk).exists())

    def test_foreign_team_update_and_delete_are_denied(self):
        self.post(
            "portal_team",
            {
                "action": "update_team",
                "team_id": self.beta_team.pk,
                "team_type": "FRC",
                "number": "202",
                "name": "Tampered Beta Team",
                "color": "#ffffff",
            },
        )
        self.post(
            "portal_team",
            {"action": "delete_team", "team_id": self.beta_team.pk},
        )

        self.beta_team.refresh_from_db()
        self.assertEqual(self.beta_team.name, "Beta Team")
        self.assertTrue(Team.objects.filter(pk=self.beta_team.pk).exists())

    def test_foreign_crew_update_and_delete_are_denied(self):
        self.post(
            "portal_crew",
            {
                "action": "update_crew",
                "crew_id": self.beta_crew.pk,
                "name": "Tampered Beta Crew",
                "color": "#ffffff",
            },
        )
        self.post(
            "portal_crew",
            {"action": "delete_crew", "crew_id": self.beta_crew.pk},
        )

        self.beta_crew.refresh_from_db()
        self.assertEqual(self.beta_crew.name, "Beta Crew")
        self.assertTrue(Crew.objects.filter(pk=self.beta_crew.pk).exists())

    def test_foreign_subteam_update_and_delete_are_denied(self):
        self.post(
            "portal_subteam",
            {
                "action": "update_subteam",
                "subteam_id": self.beta_subteam.pk,
                "name": "Tampered Beta Subteam",
                "color": "#ffffff",
            },
        )
        self.post(
            "portal_subteam",
            {"action": "delete_subteam", "subteam_id": self.beta_subteam.pk},
        )

        self.beta_subteam.refresh_from_db()
        self.assertEqual(self.beta_subteam.name, "Beta Subteam")
        self.assertTrue(SubTeam.objects.filter(pk=self.beta_subteam.pk).exists())

    def test_foreign_kiosk_toggle_and_delete_are_denied(self):
        self.post(
            "portal_kiosk",
            {
                "action": "toggle_kiosk_config",
                "kiosk_config_id": self.beta_kiosk.pk,
            },
        )
        self.post(
            "portal_kiosk",
            {
                "action": "delete_kiosk_config",
                "kiosk_config_id": self.beta_kiosk.pk,
            },
        )

        self.beta_kiosk.refresh_from_db()
        self.assertTrue(self.beta_kiosk.is_active)
        self.assertTrue(KioskConfig.objects.filter(pk=self.beta_kiosk.pk).exists())

    def test_add_actions_assign_local_owner_and_require_local_programs(self):
        self.client.raise_request_exception = False
        self.post(
            "portal_team",
            {
                "action": "add_team",
                "team_type": "FRC",
                "number": "303",
                "name": "New Alpha Team",
                "color": "#123456",
            },
        )
        self.post(
            "portal_crew",
            {
                "action": "add_crew",
                "program_id": self.alpha_program.pk,
                "name": "New Alpha Crew",
                "color": "#123456",
            },
        )
        self.post(
            "portal_subteam",
            {
                "action": "add_subteam",
                "program_id": self.alpha_program.pk,
                "name": "New Alpha Subteam",
                "color": "#123456",
            },
        )
        self.post(
            "portal_kiosk",
            {
                "action": "add_kiosk_config",
                "program_id": self.alpha_program.pk,
                "label": "New Alpha Kiosk",
            },
        )

        self.assertTrue(
            Team.objects.filter(
                organization=self.alpha, team_type="FRC", number=303
            ).exists()
        )
        self.assertTrue(
            Crew.objects.filter(
                organization=self.alpha,
                program=self.alpha_program,
                name="New Alpha Crew",
            ).exists()
        )
        self.assertTrue(
            SubTeam.objects.filter(
                organization=self.alpha,
                program=self.alpha_program,
                name="New Alpha Subteam",
            ).exists()
        )
        self.assertTrue(
            KioskConfig.objects.filter(
                organization=self.alpha,
                program=self.alpha_program,
                label="New Alpha Kiosk",
            ).exists()
        )

    def test_foreign_program_ids_are_rejected_for_program_children(self):
        self.client.raise_request_exception = False
        self.post(
            "portal_crew",
            {
                "action": "add_crew",
                "program_id": self.beta_program.pk,
                "name": "Invalid Crew",
                "color": "#123456",
            },
        )
        self.post(
            "portal_subteam",
            {
                "action": "add_subteam",
                "program_id": self.beta_program.pk,
                "name": "Invalid Subteam",
                "color": "#123456",
            },
        )
        self.post(
            "portal_kiosk",
            {
                "action": "add_kiosk_config",
                "program_id": self.beta_program.pk,
                "label": "Invalid Kiosk",
            },
        )

        self.assertFalse(Crew.objects.filter(name="Invalid Crew").exists())
        self.assertFalse(SubTeam.objects.filter(name="Invalid Subteam").exists())
        self.assertFalse(KioskConfig.objects.filter(label="Invalid Kiosk").exists())

    def test_deleting_local_team_does_not_change_foreign_enrollment(self):
        beta_student = Student.objects.create(
            organization=self.beta,
            legal_first_name="Beta",
            last_name="Student",
        )
        enrollment = Enrollment.objects.create(
            organization=self.beta,
            student=beta_student,
            program=self.beta_program,
            team=self.alpha_team,
        )

        self.post(
            "portal_team",
            {"action": "delete_team", "team_id": self.alpha_team.pk},
        )

        self.assertTrue(Team.objects.filter(pk=self.alpha_team.pk).exists())
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.team_id, self.alpha_team.pk)

    def test_deleting_local_crew_or_subteam_does_not_change_foreign_enrollment(self):
        beta_student = Student.objects.create(
            organization=self.beta,
            legal_first_name="Beta",
            last_name="Student",
        )
        enrollment = Enrollment.objects.create(
            organization=self.beta,
            student=beta_student,
            program=self.beta_program,
            crew=self.alpha_crew,
            subteam=self.alpha_subteam,
        )

        self.post(
            "portal_crew",
            {"action": "delete_crew", "crew_id": self.alpha_crew.pk},
        )
        self.post(
            "portal_subteam",
            {"action": "delete_subteam", "subteam_id": self.alpha_subteam.pk},
        )

        self.assertTrue(Crew.objects.filter(pk=self.alpha_crew.pk).exists())
        self.assertTrue(SubTeam.objects.filter(pk=self.alpha_subteam.pk).exists())
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.crew_id, self.alpha_crew.pk)
        self.assertEqual(enrollment.subteam_id, self.alpha_subteam.pk)
