from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from programs.models import Crew, Enrollment, Program, Student


class CrewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="leadmentor", password="password")
        self.lead_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.user.groups.add(self.lead_group)
        self.client.login(username="leadmentor", password="password")

        self.program = Program.objects.create(name="FLL 2025", year=2025)
        self.student = Student.objects.create(legal_first_name="Alex", last_name="Smith")
        self.enrollment = Enrollment.objects.create(student=self.student, program=self.program)

    def test_add_crew(self):
        url = reverse("portal_settings")
        response = self.client.post(url, {
            "action": "add_crew",
            "program_id": self.program.id,
            "name": "Chassis",
            "color": "#00ff00"
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Crew.objects.filter(name="Chassis", program=self.program).exists())

    def test_update_crew(self):
        crew = Crew.objects.create(name="Old Name", program=self.program, color="#000000")
        url = reverse("portal_settings")
        response = self.client.post(url, {
            "action": "update_crew",
            "crew_id": crew.id,
            "name": "New Name",
            "color": "#ffffff"
        })
        self.assertEqual(response.status_code, 302)
        crew.refresh_from_db()
        self.assertEqual(crew.name, "New Name")
        self.assertEqual(crew.color, "#ffffff")

    def test_delete_crew(self):
        crew = Crew.objects.create(name="Delete Me", program=self.program)
        url = reverse("portal_settings")
        response = self.client.post(url, {
            "action": "delete_crew",
            "crew_id": crew.id
        })
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Crew.objects.filter(id=crew.id).exists())

    def test_assign_crew_to_enrollment(self):
        crew = Crew.objects.create(name="Programming", program=self.program)
        url = reverse("program_enrollment_update", args=[self.program.id])
        response = self.client.post(url, {
            "enrollment_id": self.enrollment.id,
            "crew_id": crew.id
        })
        self.assertEqual(response.status_code, 302)
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.crew, crew)

    def test_unassign_crew_from_enrollment(self):
        crew = Crew.objects.create(name="Programming", program=self.program)
        self.enrollment.crew = crew
        self.enrollment.save()
        
        url = reverse("program_enrollment_update", args=[self.program.id])
        response = self.client.post(url, {
            "enrollment_id": self.enrollment.id,
            "crew_id": ""
        })
        self.assertEqual(response.status_code, 302)
        self.enrollment.refresh_from_db()
        self.assertIsNone(self.enrollment.crew)
