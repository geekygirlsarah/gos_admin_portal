"""Reproducer / tests for mentor team assignment toggle (TDD)."""

from datetime import timedelta

from django.contrib.auth.models import Group as AuthGroup
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from organizations.models import Organization, UserOrganizationMembership
from programs.models import (
    Adult,
    Enrollment,
)
from programs.models import Group as ProgramGroup
from programs.models import (
    Program,
    RolePermission,
    Student,
    Team,
)


class TeamAssignmentOrganizationIsolationTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.get(slug="gos")
        self.other_organization = Organization.objects.create(
            name="Other Organization", slug="other-assignments"
        )
        self.user = User.objects.create_user(username="org-lead")
        UserOrganizationMembership.objects.create(
            user=self.user, organization=self.organization, role="LeadMentor"
        )
        self.client.force_login(self.user)
        self.program = Program.objects.create(
            name="Our program", organization=self.organization
        )
        self.other_program = Program.objects.create(
            name="Other program", organization=self.other_organization
        )
        self.student = Student.objects.create(
            legal_first_name="Our", last_name="Student", organization=self.organization
        )
        self.other_student = Student.objects.create(
            legal_first_name="Other",
            last_name="Student",
            organization=self.other_organization,
        )
        self.enrollment = Enrollment.objects.create(
            student=self.student, program=self.program, organization=self.organization
        )
        self.other_enrollment = Enrollment.objects.create(
            student=self.other_student,
            program=self.other_program,
            organization=self.other_organization,
        )
        self.team = Team.objects.create(
            team_type="FRC", number=1, organization=self.organization
        )
        self.other_team = Team.objects.create(
            team_type="FRC", number=2, organization=self.other_organization
        )
        self.other_group = ProgramGroup.objects.create(
            name="Other group",
            program=self.other_program,
            organization=self.other_organization,
        )

    def test_other_organization_program_cannot_be_accessed_or_changed(self):
        assignment_url = reverse("program_assignment", args=[self.other_program.pk])
        response = self.client.get(assignment_url)
        self.assertRedirects(response, reverse("home"), fetch_redirect_response=False)

        response = self.client.post(
            reverse("program_enrollment_update", args=[self.other_program.pk]),
            {"enrollment_id": self.other_enrollment.pk, "active": "false"},
        )
        self.assertRedirects(response, reverse("home"), fetch_redirect_response=False)
        self.other_enrollment.refresh_from_db()
        self.assertTrue(self.other_enrollment.active)

        response = self.client.post(
            assignment_url,
            {
                "assignment_type": "team",
                "target_id": self.team.pk,
                "student_ids": [self.other_student.pk],
            },
        )
        self.assertRedirects(response, reverse("home"), fetch_redirect_response=False)
        self.other_enrollment.refresh_from_db()
        self.assertIsNone(self.other_enrollment.team)

    def test_assignment_choices_do_not_include_other_organization_teams(self):
        response = self.client.get(
            reverse("program_assignment", args=[self.program.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.team, response.context["teams"])
        self.assertNotIn(self.other_team, response.context["teams"])
        self.assertNotIn(self.other_group, response.context["groups"])

    def test_other_organization_team_cannot_be_assigned(self):
        for url_name, payload in (
            (
                "program_enrollment_update",
                {"enrollment_id": self.enrollment.pk, "team_id": self.other_team.pk},
            ),
            (
                "program_assignment",
                {
                    "assignment_type": "team",
                    "target_id": self.other_team.pk,
                    "student_ids": [self.student.pk],
                },
            ),
        ):
            with self.subTest(url_name=url_name):
                response = self.client.post(
                    reverse(url_name, args=[self.program.pk]), payload
                )
                self.assertRedirects(
                    response, reverse("home"), fetch_redirect_response=False
                )
                self.enrollment.refresh_from_db()
                self.assertIsNone(self.enrollment.team)

    def test_other_organization_group_cannot_be_assigned(self):
        response = self.client.post(
            reverse("program_assignment", args=[self.program.pk]),
            {
                "assignment_type": "group",
                "target_id": self.other_group.pk,
                "student_ids": [self.student.pk],
            },
        )
        self.assertRedirects(
            response,
            reverse("program_assignment", args=[self.program.pk]),
            fetch_redirect_response=False,
        )
        self.enrollment.refresh_from_db()
        self.assertFalse(self.enrollment.groups.exists())

    def test_mismatched_student_enrollment_is_not_displayed_or_updated(self):
        enrollment = Enrollment.objects.create(
            student=self.other_student,
            program=self.program,
            organization=self.organization,
        )
        url = reverse("program_assignment", args=[self.program.pk])
        response = self.client.get(url)
        self.assertNotIn(enrollment, response.context["enrollments"])
        response = self.client.post(
            reverse("program_enrollment_update", args=[self.program.pk]),
            {"enrollment_id": enrollment.pk, "active": "false"},
        )
        self.assertRedirects(response, reverse("home"), fetch_redirect_response=False)
        enrollment.refresh_from_db()
        self.assertTrue(enrollment.active)


class MentorTeamAssignmentPermissionTests(TestCase):
    def setUp(self):
        self.today = timezone.now().date()
        self.active_program = Program.objects.create(
            name="Active Prog",
            active=True,
            start_date=self.today - timedelta(days=5),
            end_date=self.today + timedelta(days=5),
        )
        self.upcoming_program = Program.objects.create(
            name="Upcoming Prog",
            active=True,
            start_date=self.today + timedelta(days=10),
            end_date=self.today + timedelta(days=20),
        )
        self.past_program = Program.objects.create(
            name="Past Prog",
            active=True,
            start_date=self.today - timedelta(days=20),
            end_date=self.today - timedelta(days=5),
        )
        self.inactive_program = Program.objects.create(
            name="Inactive Prog",
            active=False,
            start_date=self.today - timedelta(days=5),
            end_date=self.today + timedelta(days=5),
        )
        self.team = Team.objects.create(team_type="FRC", number=9999, color="#ff0000")
        self.student1 = Student.objects.create(
            legal_first_name="A", last_name="Student"
        )
        self.student2 = Student.objects.create(
            legal_first_name="B", last_name="Student"
        )
        for prog in [
            self.active_program,
            self.upcoming_program,
            self.past_program,
            self.inactive_program,
        ]:
            Enrollment.objects.create(student=self.student1, program=prog)
            Enrollment.objects.create(student=self.student2, program=prog)

        # Mentor user
        self.mentor_user = User.objects.create_user(
            username="mentor", password="pass123"
        )  # nosec B106
        self.mentor_adult = Adult.objects.create(
            user=self.mentor_user,
            legal_first_name="M",
            last_name="Mentor",
            is_mentor=True,
            mentor_active=True,
        )

        # Lead mentor
        self.lead_user = User.objects.create_user(
            username="lead", password="pass123"
        )  # nosec B106
        self.lead_group, _ = AuthGroup.objects.get_or_create(name="LeadMentor")
        self.lead_user.groups.add(self.lead_group)
        UserOrganizationMembership.objects.create(
            user=self.lead_user,
            organization=self.active_program.organization,
            role="LeadMentor",
        )

        # Ensure RolePermission rows exist
        for role in ["Mentor", "Parent", "Student", "Alumni"]:
            for sec, _ in RolePermission.SECTION_CHOICES:
                RolePermission.objects.get_or_create(role=role, section=sec)

        # Default: deny mentors team assignments
        RolePermission.objects.filter(role="Mentor", section="team_assignments").update(
            can_write=False, can_read=False
        )

    def _enable_mentor_assign(self):
        RolePermission.objects.filter(role="Mentor", section="team_assignments").update(
            can_write=True, can_read=True
        )

    def test_mentor_blocked_without_permission_for_active_program(self):
        self.client.login(username="mentor", password="pass123")  # nosec B106
        url = reverse("program_assignment", args=[self.active_program.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("home"))
        # POST also blocked
        resp = self.client.post(
            url,
            {
                "assignment_type": "team",
                "target_id": self.team.id,
                "student_ids": [self.student1.id],
            },
        )
        self.assertEqual(resp.status_code, 302)

    def test_mentor_allowed_with_permission_for_active_program(self):
        self._enable_mentor_assign()
        self.client.login(username="mentor", password="pass123")  # nosec B106
        url = reverse("program_assignment", args=[self.active_program.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        # Bulk assign
        resp = self.client.post(
            url,
            {
                "assignment_type": "team",
                "target_id": self.team.id,
                "student_ids": [self.student1.id],
            },
        )
        self.assertEqual(resp.status_code, 302)
        en = Enrollment.objects.get(student=self.student1, program=self.active_program)
        self.assertEqual(en.team, self.team)

    def test_mentor_allowed_for_upcoming_program(self):
        self._enable_mentor_assign()
        self.client.login(username="mentor", password="pass123")  # nosec B106
        url = reverse("program_assignment", args=[self.upcoming_program.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.team2 = Team.objects.create(team_type="FRC", number=1111)
        resp = self.client.post(
            url,
            {
                "assignment_type": "team",
                "target_id": self.team2.id,
                "student_ids": [self.student1.id],
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            Enrollment.objects.get(
                student=self.student1, program=self.upcoming_program
            ).team,
            self.team2,
        )

    def test_mentor_blocked_for_past_program_even_with_permission(self):
        self._enable_mentor_assign()
        self.client.login(username="mentor", password="pass123")  # nosec B106
        for prog in [self.past_program, self.inactive_program]:
            url = reverse("program_assignment", args=[prog.pk])
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 302, f"GET should block {prog.name}")
            resp = self.client.post(
                url,
                {
                    "assignment_type": "team",
                    "target_id": self.team.id,
                    "student_ids": [self.student1.id],
                },
            )
            self.assertEqual(resp.status_code, 302, f"POST should block {prog.name}")

    def test_mentor_blocked_for_enrollment_update_on_past_program(self):
        self._enable_mentor_assign()
        self.client.login(username="mentor", password="pass123")  # nosec B106
        enrollment = Enrollment.objects.get(
            student=self.student1, program=self.past_program
        )
        url = reverse("program_enrollment_update", args=[self.past_program.pk])
        resp = self.client.post(
            url, {"enrollment_id": enrollment.id, "team_id": self.team.id}
        )
        self.assertEqual(resp.status_code, 302)
        enrollment.refresh_from_db()
        self.assertIsNone(enrollment.team)

    def test_mentor_can_update_team_via_enrollment_update_on_active(self):
        self._enable_mentor_assign()
        self.client.login(username="mentor", password="pass123")  # nosec B106
        enrollment = Enrollment.objects.get(
            student=self.student1, program=self.active_program
        )
        url = reverse("program_enrollment_update", args=[self.active_program.pk])
        resp = self.client.post(
            url, {"enrollment_id": enrollment.id, "team_id": self.team.id}
        )
        self.assertEqual(resp.status_code, 302)
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.team, self.team)

    def test_mentor_cannot_toggle_active_status(self):
        self._enable_mentor_assign()
        self.client.login(username="mentor", password="pass123")  # nosec B106
        enrollment = Enrollment.objects.get(
            student=self.student1, program=self.active_program
        )
        self.assertTrue(enrollment.active)
        url = reverse("program_enrollment_update", args=[self.active_program.pk])
        resp = self.client.post(
            url, {"enrollment_id": enrollment.id, "active": "false"}
        )
        self.assertEqual(resp.status_code, 302)
        enrollment.refresh_from_db()
        # Should remain active because mentors cannot toggle
        self.assertTrue(enrollment.active)

    def test_lead_mentor_can_still_access_past_program(self):
        self.client.login(username="lead", password="pass123")  # nosec B106
        url = reverse("program_assignment", args=[self.past_program.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        resp = self.client.post(
            url,
            {
                "assignment_type": "team",
                "target_id": self.team.id,
                "student_ids": [self.student1.id],
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            Enrollment.objects.get(
                student=self.student1, program=self.past_program
            ).team,
            self.team,
        )

    def test_detail_button_visible_for_mentor_with_permission_on_active(self):
        self._enable_mentor_assign()
        self.client.login(username="mentor", password="pass123")  # nosec B106
        url = reverse("program_detail", args=[self.active_program.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Assign Teams/Crews")

    def test_detail_button_hidden_for_mentor_on_past_program(self):
        self._enable_mentor_assign()
        self.client.login(username="mentor", password="pass123")  # nosec B106
        url = reverse("program_detail", args=[self.past_program.pk])
        # Past program detail itself is blocked for mentors (can_user_read programs)
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 302)

    def test_detail_button_hidden_without_permission(self):
        self.client.login(username="mentor", password="pass123")  # nosec B106
        url = reverse("program_detail", args=[self.active_program.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Assign Teams/Crews")
