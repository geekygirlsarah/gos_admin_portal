"""Tests for the unified Program Messaging interface and subgroup filtering.

Covers:
- Unified ProgramEmailView accessible from Admin (global) and Program Detail.
- Role-based program visibility (Lead Mentors see all programs, Mentors see readable programs).
- Pre-selection of program via URL parameter or query string.
- Subgroup filtering by Team, Crew/Project, and SubTeam for Students and Parents.
- Management command get_program_emails with subgroup filtering flags.
- UI elements, confirmation modal markers, and CSP nonce compliance.
"""

from io import StringIO

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from programs.models import Adult, Crew, Enrollment, Program, Student, SubTeam, Team


class MessagingUIImprovementsTests(TestCase):
    def setUp(self):
        # Create groups
        self.lead_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.mentor_group, _ = Group.objects.get_or_create(name="Mentor")

        # Create users
        self.lead_user = User.objects.create_superuser(
            username="lead_mentor",
            email="lead@example.com",
            password="password123",  # nosec B106
        )
        self.mentor_user = User.objects.create_user(
            username="mentor_user",
            email="mentor@example.com",
            password="password123",  # nosec B106
        )
        self.mentor_user.groups.add(self.mentor_group)

        # Create programs
        self.active_program = Program.objects.create(name="FRC 2026-2027", active=True)
        self.upcoming_program = Program.objects.create(
            name="Summer Camp 2027", active=True
        )
        self.inactive_program = Program.objects.create(
            name="Past Season 2024", active=False
        )

        # Create teams, crews, and subteams
        self.team_frc = Team.objects.create(
            team_type="FRC", number=3504, name="Girls of Steel"
        )
        self.team_ftc = Team.objects.create(
            team_type="FTC", number=8911, name="Junior Steel"
        )

        self.crew_mech = Crew.objects.create(
            name="Mechanical", program=self.active_program, color="#0d6efd"
        )
        self.crew_soft = Crew.objects.create(
            name="Software", program=self.active_program, color="#198754"
        )

        self.subteam_chassis = SubTeam.objects.create(
            name="Chassis", program=self.active_program, color="#6f42c1"
        )
        self.subteam_intake = SubTeam.objects.create(
            name="Intake", program=self.active_program, color="#ffc107"
        )

        # Create students
        self.student_mech = Student.objects.create(
            legal_first_name="Maya",
            last_name="Mech",
            personal_email="maya@example.com",
        )
        self.student_soft = Student.objects.create(
            legal_first_name="Sofia",
            last_name="Soft",
            personal_email="sofia@example.com",
        )
        self.student_unassigned = Student.objects.create(
            legal_first_name="Uma",
            last_name="Unassigned",
            personal_email="uma@example.com",
        )

        # Enrollments
        Enrollment.objects.create(
            student=self.student_mech,
            program=self.active_program,
            team=self.team_frc,
            crew=self.crew_mech,
            subteam=self.subteam_chassis,
            active=True,
        )
        Enrollment.objects.create(
            student=self.student_soft,
            program=self.active_program,
            team=self.team_ftc,
            crew=self.crew_soft,
            subteam=self.subteam_intake,
            active=True,
        )
        Enrollment.objects.create(
            student=self.student_unassigned,
            program=self.active_program,
            active=True,
        )

        # Parents
        self.parent_mech = Adult.objects.create(
            legal_first_name="Peter",
            last_name="Mech",
            personal_email="peter_parent@example.com",
            is_parent=True,
            email_updates=True,
            login_enabled=True,
        )
        self.parent_mech.students.add(self.student_mech)

        self.parent_soft = Adult.objects.create(
            legal_first_name="Sarah",
            last_name="Soft",
            personal_email="sarah_parent@example.com",
            is_parent=True,
            email_updates=True,
            login_enabled=True,
        )
        self.parent_soft.students.add(self.student_soft)

        # Mentor adult
        self.adult_mentor = Adult.objects.create(
            legal_first_name="Mitch",
            last_name="Mentor",
            personal_email="mitch_mentor@example.com",
            is_mentor=True,
            mentor_active=True,
            login_enabled=True,
        )

    def test_lead_mentor_sees_all_programs_in_dropdown(self):
        self.client.login(username="lead_mentor", password="password123")  # nosec B106
        response = self.client.get(reverse("program_messaging"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "FRC 2026-2027")
        self.assertContains(response, "Summer Camp 2027")
        self.assertContains(response, "Past Season 2024")

    def test_mentor_only_sees_active_readable_programs(self):
        self.client.login(username="mentor_user", password="password123")  # nosec B106
        response = self.client.get(reverse("program_messaging"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "FRC 2026-2027")
        self.assertContains(response, "Summer Camp 2027")
        self.assertNotContains(response, "Past Season 2024")

    def test_preselection_via_program_email_url(self):
        self.client.login(username="lead_mentor", password="password123")  # nosec B106
        response = self.client.get(
            reverse("program_email", args=[self.active_program.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'value="{self.active_program.pk}"')
        self.assertContains(response, "FRC 2026-2027")

    def test_preselection_via_query_param(self):
        self.client.login(username="lead_mentor", password="password123")  # nosec B106
        response = self.client.get(
            f"{reverse('program_messaging')}?program={self.active_program.pk}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'value="{self.active_program.pk}"')

    def test_send_email_all_students_and_parents(self):
        self.client.login(username="lead_mentor", password="password123")  # nosec B106
        post_data = {
            "program": self.active_program.pk,
            "recipient_groups": ["students", "parents"],
            "subject": "All Hands Meeting",
            "body": "<p>Hello all!</p>",
            "from_account": "DEFAULT",
        }
        response = self.client.post(
            reverse("program_messaging"), data=post_data, follow=True
        )
        self.assertEqual(response.status_code, 200)
        # 3 students + 2 parents = 5 recipients
        self.assertContains(response, "Email sent to 5 recipient(s)")

    def test_subgroup_filter_by_crew(self):
        self.client.login(username="lead_mentor", password="password123")  # nosec B106
        post_data = {
            "program": self.active_program.pk,
            "recipient_groups": ["students", "parents"],
            "crews": [self.crew_mech.pk],
            "subject": "Mechanical Crew Meeting",
            "body": "<p>Mech meeting today</p>",
            "from_account": "DEFAULT",
        }
        response = self.client.post(
            reverse("program_messaging"), data=post_data, follow=True
        )
        self.assertEqual(response.status_code, 200)
        # Maya (student) + Peter (parent) = 2 recipients
        self.assertContains(response, "Email sent to 2 recipient(s)")

    def test_subgroup_filter_by_team(self):
        self.client.login(username="lead_mentor", password="password123")  # nosec B106
        post_data = {
            "program": self.active_program.pk,
            "recipient_groups": ["students"],
            "teams": [self.team_ftc.pk],
            "subject": "FTC Team Notice",
            "body": "<p>FTC announcement</p>",
            "from_account": "DEFAULT",
        }
        response = self.client.post(
            reverse("program_messaging"), data=post_data, follow=True
        )
        self.assertEqual(response.status_code, 200)
        # Only Sofia (student on FTC 8911)
        self.assertContains(response, "Email sent to 1 recipient(s)")

    def test_subgroup_filter_by_subteam(self):
        self.client.login(username="lead_mentor", password="password123")  # nosec B106
        post_data = {
            "program": self.active_program.pk,
            "recipient_groups": ["students", "parents"],
            "subteams": [self.subteam_chassis.pk],
            "subject": "Chassis Subteam Notice",
            "body": "<p>Chassis announcement</p>",
            "from_account": "DEFAULT",
        }
        response = self.client.post(
            reverse("program_messaging"), data=post_data, follow=True
        )
        self.assertEqual(response.status_code, 200)
        # Maya + Peter = 2 recipients
        self.assertContains(response, "Email sent to 2 recipient(s)")

    def test_command_get_program_emails_with_subgroup_filters(self):
        out = StringIO()
        call_command(
            "get_program_emails",
            "--program-id",
            self.active_program.pk,
            "--students",
            "--crew-id",
            self.crew_mech.pk,
            stdout=out,
        )
        output = out.getvalue().strip()
        emails = [e.strip() for e in output.split(",")]
        self.assertIn("maya@example.com", emails)
        self.assertNotIn("sofia@example.com", emails)
        self.assertNotIn("uma@example.com", emails)

    def test_ui_contains_subgroup_filter_sections_and_csp_nonce(self):
        self.client.login(username="lead_mentor", password="password123")  # nosec B106
        response = self.client.get(reverse("program_messaging"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Audience Type")
        self.assertContains(response, "Broadcast to Program")
        self.assertContains(response, "Send Test / Preview Email")
        self.assertContains(
            response,
            "Parents of enrolled students (or selected subgroups) who opted in to email updates.",
        )
        self.assertContains(response, "Filter by Subgroups")
        self.assertContains(response, "Teams")
        self.assertContains(response, "Crews / Projects")
        self.assertContains(response, "SubTeams")
        self.assertContains(response, 'id="emailSendConfirmModal"')
        # Ensure scripts have nonce
        self.assertContains(response, 'nonce="')
        self.assertNotContains(response, "onclick=")

    def test_send_test_email_without_program_or_recipient_groups(self):
        self.client.login(username="lead_mentor", password="password123")  # nosec B106
        post_data = {
            "test_email": "preview@example.com",
            "subject": "Formatting Preview",
            "body": "<p>Previewing email</p>",
            "from_account": "DEFAULT",
        }
        response = self.client.post(
            reverse("program_messaging"), data=post_data, follow=True
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Email sent to 1 recipient(s) (test only).")

    def test_send_broadcast_validation_requires_program_and_groups(self):
        self.client.login(username="lead_mentor", password="password123")  # nosec B106
        post_data = {
            "subject": "Missing Program and Groups",
            "body": "<p>Test body</p>",
            "from_account": "DEFAULT",
        }
        response = self.client.post(
            reverse("program_messaging"), data=post_data, follow=True
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Program is required when sending a broadcast.")
        self.assertContains(
            response,
            "Please select at least one recipient group when sending a broadcast.",
        )
