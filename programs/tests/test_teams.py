from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from programs.models import Enrollment, Program, Student, Team, RolePermission

class TeamSettingsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(username="admin", password="password", email="admin@example.com")
        self.client.login(username="admin", password="password")
        self.lead_mentor_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.user.groups.add(self.lead_mentor_group)

    def test_settings_page_accessible(self):
        url = reverse("portal_settings")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "programs/settings.html")

    def test_add_team(self):
        url = reverse("portal_settings")
        resp = self.client.post(url, {
            "action": "add_team",
            "team_type": "FRC",
            "number": "3054",
            "name": "Girls of Steel",
            "color": "#ff0000"
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(Team.objects.filter(team_type="FRC", number=3054, name="Girls of Steel").exists())
        team = Team.objects.get(team_type="FRC", number=3054)
        self.assertEqual(team.color, "#ff0000")
        self.assertEqual(team.name, "Girls of Steel")

    def test_update_team(self):
        team = Team.objects.create(team_type="FTC", number=9820, name="Original Name", color="#0000ff")
        url = reverse("portal_settings")
        resp = self.client.post(url, {
            "action": "update_team",
            "team_id": team.id,
            "team_type": "FTC",
            "number": "9820",
            "name": "New Name",
            "color": "#00ff00"
        })
        self.assertEqual(resp.status_code, 302)
        team.refresh_from_db()
        self.assertEqual(team.color, "#00ff00")
        self.assertEqual(team.name, "New Name")

    def test_delete_team(self):
        team = Team.objects.create(team_type="FLL_CHALLENGE", number=1234, color="#0000ff")
        url = reverse("portal_settings")
        resp = self.client.post(url, {
            "action": "delete_team",
            "team_id": team.id
        })
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(Team.objects.filter(id=team.id).exists())

    def test_assign_team_to_student_in_program(self):
        program = Program.objects.create(name="Summer 2025")
        student = Student.objects.create(legal_first_name="Test", last_name="Student")
        enrollment = Enrollment.objects.create(program=program, student=student)
        team = Team.objects.create(team_type="FRC", number=3054, color="#ff0000")

        url = reverse("program_enrollment_update", args=[program.pk])
        resp = self.client.post(url, {
            "enrollment_id": enrollment.id,
            "team_id": team.id
        })
        self.assertEqual(resp.status_code, 302)
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.team, team)

    def test_permissions_update(self):
        # Ensure at least one RolePermission exists
        perm, _ = RolePermission.objects.get_or_create(role="Mentor", section="student_info")
        perm.can_read = False
        perm.save()

        url = reverse("portal_settings")
        resp = self.client.post(url, {
            "action": "update_permissions",
            f"read_{perm.id}": "on"
        })
        self.assertEqual(resp.status_code, 302)
        perm.refresh_from_db()
        self.assertTrue(perm.can_read)
        self.assertFalse(perm.can_write)
