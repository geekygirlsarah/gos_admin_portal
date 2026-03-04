from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from programs.models import Crew, Enrollment, Program, Student, Team


class AssignmentTests(TestCase):
    def setUp(self):
        self.password = "password"
        self.user = User.objects.create_user(username="leadmentor", password=self.password)
        self.lead_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.user.groups.add(self.lead_group)
        self.client.login(username="leadmentor", password=self.password)

        self.program = Program.objects.create(name="FLL 2025", year=2025)
        self.student1 = Student.objects.create(legal_first_name="Alex", last_name="Smith")
        self.student2 = Student.objects.create(legal_first_name="Bob", last_name="Jones")
        self.enrollment1 = Enrollment.objects.create(student=self.student1, program=self.program)
        self.enrollment2 = Enrollment.objects.create(student=self.student2, program=self.program)

        self.team = Team.objects.create(team_type="FLL_CHALLENGE", number=123, name="Cool Bots")
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
