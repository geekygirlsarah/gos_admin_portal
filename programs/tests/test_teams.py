"""Team, Crew, and SubTeam management tests."""

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from programs.models import (
    Crew,
    Enrollment,
    Program,
    ProgramFeature,
    Student,
    SubTeam,
    Team,
)


class TeamSettingsTests(TestCase):
    def setUp(self):
        self.password = "password"  # nosec B105
        self.user = User.objects.create_superuser(
            username="admin", password=self.password, email="admin@example.com"
        )
        self.client.login(username="admin", password=self.password)
        self.lead_mentor_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.user.groups.add(self.lead_mentor_group)

    def test_settings_page_accessible(self):
        url = reverse("portal_settings")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "programs/settings.html")

    def test_add_team(self):
        url = reverse("portal_team")
        resp = self.client.post(
            url,
            {
                "action": "add_team",
                "team_type": "FRC",
                "number": "3054",
                "name": "Girls of Steel",
                "color": "#ff0000",
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(
            Team.objects.filter(
                team_type="FRC", number=3054, name="Girls of Steel"
            ).exists()
        )
        team = Team.objects.get(team_type="FRC", number=3054)
        self.assertEqual(team.color, "#ff0000")
        self.assertEqual(team.name, "Girls of Steel")

    def test_update_team(self):
        team = Team.objects.create(
            team_type="FTC", number=9820, name="Original Name", color="#0000ff"
        )
        url = reverse("portal_team")
        resp = self.client.post(
            url,
            {
                "action": "update_team",
                "team_id": team.id,
                "team_type": "FTC",
                "number": "9820",
                "name": "New Name",
                "color": "#00ff00",
            },
        )
        self.assertEqual(resp.status_code, 302)
        team.refresh_from_db()
        self.assertEqual(team.color, "#00ff00")
        self.assertEqual(team.name, "New Name")

    def test_delete_team(self):
        team = Team.objects.create(
            team_type="FLL_CHALLENGE", number=1234, color="#0000ff"
        )
        url = reverse("portal_team")
        resp = self.client.post(url, {"action": "delete_team", "team_id": team.id})
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(Team.objects.filter(id=team.id).exists())

    def test_assign_team_to_student_in_program(self):
        program = Program.objects.create(name="Summer 2025")
        student = Student.objects.create(legal_first_name="Test", last_name="Student")
        enrollment = Enrollment.objects.create(program=program, student=student)
        team = Team.objects.create(team_type="FRC", number=3054, color="#ff0000")
        url = reverse("program_enrollment_update", args=[program.pk])
        resp = self.client.post(
            url, {"enrollment_id": enrollment.id, "team_id": team.id}
        )
        self.assertEqual(resp.status_code, 302)
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.team, team)

    def test_attendance_import_program_option_includes_date_range(self):
        attendance_feature, _ = ProgramFeature.objects.get_or_create(
            key="attendance", defaults={"name": "Attendance"}
        )
        program = Program.objects.create(
            name="Build Season",
            start_date="2026-01-10",
            end_date="2026-04-20",
        )
        program.features.add(attendance_feature)
        resp = self.client.get(reverse("portal_settings") + "?tab=imports")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(
            resp,
            f'<option value="{program.id}">Build Season (2026-01-10 - 2026-04-20)</option>',
            html=True,
        )


class CrewTests(TestCase):
    def setUp(self):
        self.password = "password"  # nosec B105
        self.user = User.objects.create_user(
            username="leadmentor", password=self.password
        )
        self.lead_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.user.groups.add(self.lead_group)
        self.client.login(username="leadmentor", password=self.password)
        self.program = Program.objects.create(name="FLL 2025")
        self.student = Student.objects.create(
            legal_first_name="Alex", last_name="Smith"
        )
        self.enrollment = Enrollment.objects.create(
            student=self.student, program=self.program
        )

    def test_add_crew(self):
        url = reverse("portal_crew")
        response = self.client.post(
            url,
            {
                "action": "add_crew",
                "program_id": self.program.id,
                "name": "Chassis",
                "color": "#00ff00",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            Crew.objects.filter(name="Chassis", program=self.program).exists()
        )

    def test_update_crew(self):
        crew = Crew.objects.create(
            name="Old Name", program=self.program, color="#000000"
        )
        url = reverse("portal_crew")
        response = self.client.post(
            url,
            {
                "action": "update_crew",
                "crew_id": crew.id,
                "name": "New Name",
                "color": "#ffffff",
            },
        )
        self.assertEqual(response.status_code, 302)
        crew.refresh_from_db()
        self.assertEqual(crew.name, "New Name")
        self.assertEqual(crew.color, "#ffffff")

    def test_delete_crew(self):
        crew = Crew.objects.create(name="Delete Me", program=self.program)
        url = reverse("portal_crew")
        response = self.client.post(url, {"action": "delete_crew", "crew_id": crew.id})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Crew.objects.filter(id=crew.id).exists())

    def test_assign_crew_to_enrollment(self):
        crew = Crew.objects.create(name="Programming", program=self.program)
        url = reverse("program_enrollment_update", args=[self.program.id])
        response = self.client.post(
            url, {"enrollment_id": self.enrollment.id, "crew_id": crew.id}
        )
        self.assertEqual(response.status_code, 302)
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.crew, crew)

    def test_unassign_crew_from_enrollment(self):
        crew = Crew.objects.create(name="Programming", program=self.program)
        self.enrollment.crew = crew
        self.enrollment.save()
        url = reverse("program_enrollment_update", args=[self.program.id])
        response = self.client.post(
            url, {"enrollment_id": self.enrollment.id, "crew_id": ""}
        )
        self.assertEqual(response.status_code, 302)
        self.enrollment.refresh_from_db()
        self.assertIsNone(self.enrollment.crew)


class SubTeamTests(TestCase):
    def setUp(self):
        self.program = Program.objects.create(name="Test Program")
        self.subteam = SubTeam.objects.create(
            name="Test SubTeam", program=self.program, color="#ff0000"
        )
        self.student = Student.objects.create(first_name="Test", last_name="Student")
        self.enrollment = Enrollment.objects.create(
            student=self.student, program=self.program
        )

    def test_subteam_creation(self):
        self.assertEqual(str(self.subteam), "Test SubTeam (Test Program)")

    def test_enrollment_subteam_assignment(self):
        self.enrollment.subteam = self.subteam
        self.enrollment.save()
        self.assertEqual(self.enrollment.subteam, self.subteam)


class SubTeamSettingsViewTests(TestCase):
    def setUp(self):
        self.password = "password"  # nosec B105
        self.user = User.objects.create_user(
            username="leadmentor", password=self.password
        )
        self.lead_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.user.groups.add(self.lead_group)
        self.client.login(username="leadmentor", password=self.password)
        self.program = Program.objects.create(name="FLL 2025")

    def test_add_subteam(self):
        url = reverse("portal_subteam")
        response = self.client.post(
            url,
            {
                "action": "add_subteam",
                "program_id": self.program.id,
                "name": "Design",
                "color": "#00ff00",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            SubTeam.objects.filter(name="Design", program=self.program).exists()
        )

    def test_update_subteam(self):
        subteam = SubTeam.objects.create(
            name="Old Name", program=self.program, color="#000000"
        )
        url = reverse("portal_subteam")
        response = self.client.post(
            url,
            {
                "action": "update_subteam",
                "subteam_id": subteam.id,
                "name": "New Name",
                "color": "#ffffff",
            },
        )
        self.assertEqual(response.status_code, 302)
        subteam.refresh_from_db()
        self.assertEqual(subteam.name, "New Name")
        self.assertEqual(subteam.color, "#ffffff")

    def test_delete_subteam(self):
        subteam = SubTeam.objects.create(name="Delete Me", program=self.program)
        url = reverse("portal_subteam")
        response = self.client.post(
            url, {"action": "delete_subteam", "subteam_id": subteam.id}
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(SubTeam.objects.filter(id=subteam.id).exists())


class AssignmentTests(TestCase):
    def setUp(self):
        self.password = "test_pass_123"  # nosec B105
        self.user = User.objects.create_user(
            username="leadmentor", password=self.password
        )
        self.lead_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.user.groups.add(self.lead_group)
        self.client.login(username="leadmentor", password=self.password)
        self.program = Program.objects.create(name="FLL 2025")
        self.student1 = Student.objects.create(
            legal_first_name="Alex", last_name="Smith"
        )
        self.student2 = Student.objects.create(
            legal_first_name="Bob", last_name="Jones"
        )
        self.enrollment1 = Enrollment.objects.create(
            student=self.student1, program=self.program
        )
        self.enrollment2 = Enrollment.objects.create(
            student=self.student2, program=self.program
        )
        self.team = Team.objects.create(
            team_type="FLL_CHALLENGE", number=123, name="Cool Bots"
        )
        self.crew = Crew.objects.create(name="Chassis", program=self.program)

    def test_assignment_page_get(self):
        url = reverse("program_assignment", args=[self.program.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.student1.last_name)
        self.assertContains(response, self.student2.last_name)
        self.assertContains(response, self.team.name)
        self.assertContains(response, self.crew.name)

    def test_bulk_assign_team(self):
        url = reverse("program_assignment", args=[self.program.id])
        data = {
            "assignment_type": "team",
            "target_id": self.team.id,
            "student_ids": [self.student1.id, self.student2.id],
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        self.enrollment1.refresh_from_db()
        self.enrollment2.refresh_from_db()
        self.assertEqual(self.enrollment1.team, self.team)
        self.assertEqual(self.enrollment2.team, self.team)

    def test_bulk_assign_crew(self):
        url = reverse("program_assignment", args=[self.program.id])
        data = {
            "assignment_type": "crew",
            "target_id": self.crew.id,
            "student_ids": [self.student1.id],
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        self.enrollment1.refresh_from_db()
        self.enrollment2.refresh_from_db()
        self.assertEqual(self.enrollment1.crew, self.crew)
        self.assertIsNone(self.enrollment2.crew)

    def test_bulk_unassign_team(self):
        self.enrollment1.team = self.team
        self.enrollment1.save()
        url = reverse("program_assignment", args=[self.program.id])
        data = {
            "assignment_type": "team",
            "target_id": "",
            "student_ids": [self.student1.id],
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        self.enrollment1.refresh_from_db()
        self.assertIsNone(self.enrollment1.team)

    def test_bulk_unassign_crew(self):
        self.enrollment1.crew = self.crew
        self.enrollment1.save()
        url = reverse("program_assignment", args=[self.program.id])
        data = {
            "assignment_type": "crew",
            "target_id": "",
            "student_ids": [self.student1.id],
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        self.enrollment1.refresh_from_db()
        self.assertIsNone(self.enrollment1.crew)

    def test_bulk_unassign_subteam(self):
        subteam = SubTeam.objects.create(name="Electrical", program=self.program)
        self.enrollment1.subteam = subteam
        self.enrollment1.save()
        url = reverse("program_assignment", args=[self.program.id])
        data = {
            "assignment_type": "subteam",
            "target_id": "",
            "student_ids": [self.student1.id],
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        self.enrollment1.refresh_from_db()
        self.assertIsNone(self.enrollment1.subteam)

    def test_bulk_unassign_no_students_selected(self):
        url = reverse("program_assignment", args=[self.program.id])
        data = {
            "assignment_type": "team",
            "target_id": "",
            "student_ids": [],
        }
        response = self.client.post(url, data, follow=True)
        self.assertEqual(response.status_code, 200)
        messages_list = list(response.context["messages"])
        self.assertTrue(any("No students selected" in str(m) for m in messages_list))
