from django.contrib.auth.models import User
from django.core import mail
from django.http import Http404
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from organizations.models import (
    Organization,
    OrganizationSettings,
    UserOrganizationMembership,
)
from programs.models import (
    Adult,
    AdultStudentRelationship,
    Crew,
    Enrollment,
    Program,
    Student,
    SubTeam,
    Team,
)
from programs.views.programs import ProgramEmailView


class ProgramMessagingTenantIsolationTests(TestCase):
    def setUp(self):
        self.gos = Organization.objects.get(slug="gos")
        self.alpha = Organization.objects.create(
            name="Messaging Alpha",
            slug="messaging-alpha",
            subdomain="messaging-alpha",
        )
        self.beta = Organization.objects.create(
            name="Messaging Beta",
            slug="messaging-beta",
            subdomain="messaging-beta",
        )
        self.host = "messaging-alpha.localhost"
        self.alpha_program = Program.objects.create(
            organization=self.alpha,
            name="Alpha Program",
        )
        self.beta_program = Program.objects.create(
            organization=self.beta,
            name="Beta Program",
        )
        self.user = User.objects.create(
            username="alpha-lead",
            email="alpha-lead@example.test",
            is_active=True,
        )
        for organization in (self.gos, self.alpha):
            UserOrganizationMembership.objects.create(
                user=self.user,
                organization=organization,
                role="LeadMentor",
                is_active=True,
            )
        self.client.force_login(self.user)
        self.configure_sender(self.gos, "gos-sender")
        self.configure_sender(self.alpha, "alpha-sender")
        self.configure_sender(self.beta, "beta-sender")

    def configure_sender(self, organization, sender_key):
        OrganizationSettings.objects.update_or_create(
            organization=organization,
            defaults={
                "data": {
                    "email_settings": {
                        "sender_accounts": [
                            {
                                "key": sender_key,
                                "email": f"{sender_key}@example.test",
                                "display_name": sender_key,
                            }
                        ]
                    }
                }
            },
        )

    def make_student(self, organization, email, name):
        return Student.objects.create(
            organization=organization,
            legal_first_name=name,
            last_name="Student",
            personal_email=email,
        )

    def make_adult(
        self, organization, email, name, *, is_parent=False, is_mentor=False
    ):
        return Adult.objects.create(
            organization=organization,
            legal_first_name=name,
            last_name="Contact",
            personal_email=email,
            is_parent=is_parent,
            is_mentor=is_mentor,
            mentor_active=is_mentor,
            email_updates=is_parent,
            login_enabled=is_parent,
        )

    def make_enrollment(self, organization, student, program, **kwargs):
        return Enrollment.objects.create(
            organization=organization,
            student=student,
            program=program,
            active=True,
            **kwargs,
        )

    def post_broadcast(self, program, groups, **extra):
        data = {
            "program": str(program.pk),
            "recipient_groups": groups,
            "subject": "Tenant announcement",
            "body": "<p>Tenant-only message</p>",
            "from_account": "DEFAULT",
            **extra,
        }
        return self.client.post(
            reverse("program_messaging"),
            data,
            HTTP_HOST=self.host,
            follow=True,
        )

    def test_program_sender_and_subgroup_choices_are_tenant_scoped(self):
        alpha_team = Team.objects.create(
            organization=self.alpha,
            team_type="FRC",
            number=101,
            name="Alpha Team",
        )
        beta_team = Team.objects.create(
            organization=self.beta,
            team_type="FRC",
            number=202,
            name="Beta Team",
        )
        alpha_crew = Crew.objects.create(
            organization=self.alpha,
            program=self.alpha_program,
            name="Alpha Crew",
        )
        beta_crew = Crew.objects.create(
            organization=self.beta,
            program=self.beta_program,
            name="Beta Crew",
        )
        alpha_subteam = SubTeam.objects.create(
            organization=self.alpha,
            program=self.alpha_program,
            name="Alpha Subteam",
        )
        beta_subteam = SubTeam.objects.create(
            organization=self.beta,
            program=self.beta_program,
            name="Beta Subteam",
        )
        alpha_student = self.make_student(self.alpha, "alpha@example.test", "Alpha")
        beta_student = self.make_student(self.beta, "beta@example.test", "Beta")
        self.make_enrollment(
            self.alpha,
            alpha_student,
            self.alpha_program,
            team=alpha_team,
            crew=alpha_crew,
            subteam=alpha_subteam,
        )
        self.make_enrollment(
            self.beta,
            beta_student,
            self.beta_program,
            team=beta_team,
            crew=beta_crew,
            subteam=beta_subteam,
        )

        response = self.client.get(reverse("program_messaging"), HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertEqual(
            list(form.fields["program"].queryset.values_list("pk", flat=True)),
            [self.alpha_program.pk],
        )
        sender_values = {value for value, label in form.fields["from_account"].choices}
        self.assertIn("alpha-sender", sender_values)
        self.assertNotIn("gos-sender", sender_values)
        self.assertNotIn("beta-sender", sender_values)
        self.assertEqual(
            set(form.fields["teams"].queryset.values_list("pk", flat=True)),
            {alpha_team.pk},
        )
        self.assertEqual(
            set(form.fields["crews"].queryset.values_list("pk", flat=True)),
            {alpha_crew.pk},
        )
        self.assertEqual(
            set(form.fields["subteams"].queryset.values_list("pk", flat=True)),
            {alpha_subteam.pk},
        )
        self.assertNotContains(response, "Beta Program")
        self.assertNotContains(response, "Beta Team")
        self.assertNotContains(response, "Beta Crew")
        self.assertNotContains(response, "Beta Subteam")

    def test_foreign_program_url_is_not_accessible(self):
        response = self.client.get(
            reverse("program_email", args=[self.beta_program.pk]),
            HTTP_HOST=self.host,
        )

        self.assertRedirects(response, reverse("home"), fetch_redirect_response=False)

    def test_missing_organization_is_rejected(self):
        request = RequestFactory().get(reverse("program_messaging"))
        request.user = self.user
        request.organization = None

        with self.assertRaises(Http404):
            ProgramEmailView.as_view()(request)

    def test_preview_uses_current_organization_sender_without_program(self):
        response = self.client.post(
            reverse("program_messaging"),
            {
                "test_email": "preview@example.test",
                "subject": "Preview",
                "body": "<p>Preview</p>",
                "from_account": "alpha-sender",
            },
            HTTP_HOST=self.host,
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("alpha-sender@example.test", mail.outbox[0].from_email)
        self.assertNotIn("gos-sender@example.test", mail.outbox[0].from_email)

    def test_preview_rejects_foreign_sender_identity(self):
        response = self.client.post(
            reverse("program_messaging"),
            {
                "test_email": "preview@example.test",
                "subject": "Preview",
                "body": "<p>Preview</p>",
                "from_account": "gos-sender",
            },
            HTTP_HOST=self.host,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mail.outbox, [])
        self.assertIn("from_account", response.context["form"].errors)

    @override_settings(
        EMAIL_SENDER_ACCOUNTS=[
            {"key": "legacy-team", "email": "legacy-team@example.test"}
        ]
    )
    def test_platform_named_sender_is_not_offered_to_unconfigured_tenant(self):
        OrganizationSettings.objects.filter(organization=self.alpha).update(
            data={"email_settings": {}}
        )
        response = self.client.get(reverse("program_messaging"), HTTP_HOST=self.host)
        choices = dict(response.context["form"].fields["from_account"].choices)

        self.assertIn("DEFAULT", choices)
        self.assertNotIn("legacy-team", choices)

        response = self.client.post(
            reverse("program_messaging"),
            {
                "test_email": "preview@example.test",
                "subject": "Preview",
                "body": "<p>Preview</p>",
                "from_account": "legacy-team",
            },
            HTTP_HOST=self.host,
        )
        self.assertIn("from_account", response.context["form"].errors)
        self.assertEqual(mail.outbox, [])

    @override_settings(
        DEFAULT_FROM_EMAIL="notifications@momentum.example",
        NOTIFICATION_FROM_EMAIL=None,
    )
    def test_default_sender_uses_platform_address_without_tenant_override(self):
        OrganizationSettings.objects.filter(organization=self.alpha).update(
            data={"email_settings": {}}
        )
        response = self.client.post(
            reverse("program_messaging"),
            {
                "test_email": "preview@example.test",
                "subject": "Preview",
                "body": "<p>Preview</p>",
                "from_account": "DEFAULT",
            },
            HTTP_HOST=self.host,
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("notifications@momentum.example", mail.outbox[0].from_email)

    @override_settings(
        DEFAULT_FROM_EMAIL="notifications@momentum.example",
        NOTIFICATION_FROM_EMAIL=None,
    )
    def test_default_sender_uses_tenant_notification_override(self):
        OrganizationSettings.objects.filter(organization=self.alpha).update(
            data={"email_settings": {"notification_from_email": "team@example.test"}}
        )
        response = self.client.post(
            reverse("program_messaging"),
            {
                "test_email": "preview@example.test",
                "subject": "Preview",
                "body": "<p>Preview</p>",
                "from_account": "DEFAULT",
            },
            HTTP_HOST=self.host,
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("team@example.test", mail.outbox[0].from_email)
        self.assertNotIn("notifications@momentum.example", mail.outbox[0].from_email)

    def test_foreign_program_submission_does_not_send(self):
        student = self.make_student(self.beta, "foreign@example.test", "Foreign")
        self.make_enrollment(self.beta, student, self.beta_program)

        response = self.post_broadcast(self.beta_program, ["students"])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mail.outbox, [])
        self.assertContains(response, "Select a valid choice")

    def test_foreign_subgroup_submission_is_rejected(self):
        foreign_crew = Crew.objects.create(
            organization=self.beta,
            program=self.alpha_program,
            name="Foreign Crew On Alpha Program",
        )

        response = self.post_broadcast(
            self.alpha_program,
            ["students"],
            crews=[str(foreign_crew.pk)],
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mail.outbox, [])
        self.assertIn("crews", response.context["form"].errors)

    def test_recipients_and_relationships_stay_with_program_organization(self):
        local_student = self.make_student(
            self.alpha, "local-student@example.test", "Local"
        )
        foreign_student = self.make_student(
            self.beta, "foreign-student@example.test", "Foreign"
        )
        self.make_enrollment(self.alpha, local_student, self.alpha_program)
        self.make_enrollment(self.beta, foreign_student, self.alpha_program)

        local_parent = self.make_adult(
            self.alpha,
            "local-parent@example.test",
            "Local Parent",
            is_parent=True,
        )
        foreign_parent = self.make_adult(
            self.beta,
            "foreign-parent@example.test",
            "Foreign Parent",
            is_parent=True,
        )
        local_adult_with_foreign_relationship = self.make_adult(
            self.alpha,
            "foreign-relationship@example.test",
            "Foreign Relationship",
            is_parent=True,
        )
        AdultStudentRelationship.objects.create(
            organization=self.alpha,
            adult=local_parent,
            student=local_student,
        )
        AdultStudentRelationship.objects.create(
            organization=self.alpha,
            adult=foreign_parent,
            student=local_student,
        )
        AdultStudentRelationship.objects.create(
            organization=self.beta,
            adult=local_adult_with_foreign_relationship,
            student=local_student,
        )

        local_mentor = self.make_adult(
            self.alpha,
            "local-mentor@example.test",
            "Local Mentor",
            is_mentor=True,
        )
        foreign_mentor = self.make_adult(
            self.beta,
            "foreign-mentor@example.test",
            "Foreign Mentor",
            is_mentor=True,
        )

        response = self.post_broadcast(
            self.alpha_program,
            ["students", "parents", "mentors"],
        )

        self.assertEqual(response.status_code, 200)
        recipients = {address for message in mail.outbox for address in message.to}
        self.assertEqual(
            recipients,
            {
                "local-student@example.test",
                "local-parent@example.test",
                "local-mentor@example.test",
            },
        )
        self.assertNotIn("foreign-student@example.test", recipients)
        self.assertNotIn("foreign-parent@example.test", recipients)
        self.assertNotIn("foreign-relationship@example.test", recipients)
        self.assertNotIn("foreign-mentor@example.test", recipients)
        self.assertNotEqual(
            local_mentor.organization_id, foreign_mentor.organization_id
        )
