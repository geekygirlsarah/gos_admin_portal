from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from programs.models import Enrollment, Program, Student, SubTeam


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
