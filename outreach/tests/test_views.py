from django.test import TestCase
from django.urls import reverse
from django.contrib.auth.models import User, Group
from programs.models import Student, Adult, School, Program, ProgramFeature
from outreach.models import OutreachEvent, OutreachSignup

class OutreachViewTest(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Test School")
        
        # Program and Feature
        self.feature, _ = ProgramFeature.objects.get_or_create(key="outreach", defaults={"name": "Outreach"})
        self.program = Program.objects.create(name="Test Program")
        self.program.features.add(self.feature)
        
        # Mentor
        self.mentor_user = User.objects.create_user(username="mentor", password="password")
        self.mentor_adult = Adult.objects.create(user=self.mentor_user, is_mentor=True, mentor_active=True)
        
        # Student
        self.student_user = User.objects.create_user(username="student", password="password")
        self.student_profile = Student.objects.create(
            user=self.student_user,
            legal_first_name="Test",
            last_name="Student",
            school=self.school,
            graduation_year=2027
        )
        
        # Parent
        self.parent_user = User.objects.create_user(username="parent", password="password")
        self.parent_adult = Adult.objects.create(user=self.parent_user, is_parent=True)
        
        self.event = OutreachEvent.objects.create(
            program=self.program,
            name="Test Event",
            location_name="Test Location",
            location_address="123 Test St",
            start_date="2026-09-01",
            start_time="10:00:00",
            end_time="12:00:00",
            max_champions=1,
            max_helpers=2
        )

    def test_list_view_accessible_to_all_logged_in(self):
        url = reverse("outreach:event_list", args=[self.program.id])
        
        # Student
        self.client.login(username="student", password="password")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        
        # Mentor
        self.client.login(username="mentor", password="password")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        
        # Parent
        self.client.login(username="parent", password="password")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

    def test_student_can_create_event(self):
        self.client.login(username="student", password="password")
        url = reverse("outreach:event_create", args=[self.program.id])
        data = {
            "name": "New Event",
            "location_name": "New Loc",
            "location_address": "456 New St",
            "start_date": "2026-09-02",
            "start_time": "14:00:00",
            "end_time": "16:00:00",
            "max_champions": 1,
            "max_helpers": 5
        }
        resp = self.client.post(url, data)
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(OutreachEvent.objects.filter(name="New Event").exists())
        # The student who created it should be a champion
        event = OutreachEvent.objects.get(name="New Event")
        self.assertEqual(event.program, self.program)
        self.assertTrue(OutreachSignup.objects.filter(event=event, student=self.student_profile, role=OutreachSignup.CHAMPION).exists())

    def test_mentor_can_create_event(self):
        self.client.login(username="mentor", password="password")
        url = reverse("outreach:event_create", args=[self.program.id])
        data = {
            "name": "Mentor Event",
            "location_name": "Loc",
            "location_address": "Addr",
            "start_date": "2026-09-03",
            "start_time": "14:00:00",
            "end_time": "16:00:00",
            "max_champions": 1,
            "max_helpers": 5
        }
        resp = self.client.post(url, data)
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(OutreachEvent.objects.filter(name="Mentor Event").exists())
        # Mentors are NOT champions
        event = OutreachEvent.objects.get(name="Mentor Event")
        self.assertEqual(event.program, self.program)
        self.assertFalse(OutreachSignup.objects.filter(event=event).exists())

    def test_parent_cannot_create_event(self):
        self.client.login(username="parent", password="password")
        url = reverse("outreach:event_create", args=[self.program.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 302)

    def test_signup_helper(self):
        self.client.login(username="student", password="password")
        url = reverse("outreach:event_signup", args=[self.program.id, self.event.pk])
        resp = self.client.post(url, {"role": OutreachSignup.HELPER})
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(OutreachSignup.objects.filter(event=self.event, student=self.student_profile, role=OutreachSignup.HELPER).exists())

    def test_signup_cancel(self):
        OutreachSignup.objects.create(event=self.event, student=self.student_profile, role=OutreachSignup.HELPER)
        self.client.login(username="student", password="password")
        url = reverse("outreach:event_cancel", args=[self.program.id, self.event.pk])
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(OutreachSignup.objects.filter(event=self.event, student=self.student_profile).exists())

    def test_champion_can_edit_event(self):
        OutreachSignup.objects.create(event=self.event, student=self.student_profile, role=OutreachSignup.CHAMPION)
        self.client.login(username="student", password="password")
        url = reverse("outreach:event_edit", args=[self.program.id, self.event.pk])
        data = {
            "name": "Updated Event",
            "location_name": "Test Location",
            "location_address": "123 Test St",
            "start_date": "2026-09-01",
            "start_time": "10:00:00",
            "end_time": "12:00:00",
            "max_champions": 1,
            "max_helpers": 2
        }
        resp = self.client.post(url, data)
        self.assertEqual(resp.status_code, 302)
        self.event.refresh_from_db()
        self.assertEqual(self.event.name, "Updated Event")

    def test_other_student_cannot_edit_event(self):
        student2_user = User.objects.create_user(username="student2", password="password")
        Student.objects.create(
            user=student2_user,
            legal_first_name="Test2",
            last_name="Student2",
            school=self.school,
            graduation_year=2027
        )
        self.client.login(username="student2", password="password")
        url = reverse("outreach:event_edit", args=[self.program.id, self.event.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 302) # Redirect to dashboard

    def test_mentor_can_edit_and_delete_event(self):
        self.client.login(username="mentor", password="password")
        # Edit
        url_edit = reverse("outreach:event_edit", args=[self.program.id, self.event.pk])
        resp = self.client.post(url_edit, {
            "name": "Mentor Updated",
            "location_name": "Test Location",
            "location_address": "123 Test St",
            "start_date": "2026-09-01",
            "start_time": "10:00:00",
            "end_time": "12:00:00",
            "max_champions": 1,
            "max_helpers": 2
        })
        self.assertEqual(resp.status_code, 302)
        self.event.refresh_from_db()
        self.assertEqual(self.event.name, "Mentor Updated")
        
        # Delete
        url_delete = reverse("outreach:event_delete", args=[self.program.id, self.event.pk])
        resp = self.client.post(url_delete)
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(OutreachEvent.objects.filter(pk=self.event.pk).exists())

    def test_parent_cannot_edit_event(self):
        self.client.login(username="parent", password="password")
        url = reverse("outreach:event_edit", args=[self.program.id, self.event.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 302)

    def test_outreach_disabled_program(self):
        # Disable outreach
        self.program.features.remove(self.feature)
        self.client.login(username="student", password="password")
        url = reverse("outreach:event_list", args=[self.program.id])
        resp = self.client.get(url)
        # In this project, 404 results in a 302 redirect to home
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("home"), resp.url)
