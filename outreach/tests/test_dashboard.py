from django.test import TestCase
from django.urls import reverse
from django.contrib.auth.models import User
from django.utils import timezone
from programs.models import Student, Adult, School, Program, ProgramFeature, Enrollment
from outreach.models import OutreachEvent, OutreachSignup

class OutreachDashboardTest(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Test School")
        self.program = Program.objects.create(name="Test Program", active=True)
        self.outreach_feature, _ = ProgramFeature.objects.get_or_create(key="outreach", defaults={"name": "Outreach"})
        self.program.features.add(self.outreach_feature)
        
        # Student
        self.student_user = User.objects.create_user(username="student", password="password")
        self.student_profile = Student.objects.create(
            user=self.student_user,
            legal_first_name="Test",
            last_name="Student",
            school=self.school,
            graduation_year=2027
        )
        self.enrollment = Enrollment.objects.create(student=self.student_profile, program=self.program, active=True)
        
        # Parent
        self.parent_user = User.objects.create_user(username="parent", password="password")
        self.parent_adult = Adult.objects.create(user=self.parent_user, is_parent=True)
        self.parent_adult.students.add(self.student_profile)
        
        self.event = OutreachEvent.objects.create(
            program=self.program,
            name="Dashboard Event",
            location_name="Loc",
            location_address="Addr",
            start_date=timezone.now().date(),
            start_time="10:00:00",
            end_time="12:00:00",
            max_champions=1,
            max_helpers=5
        )

    def test_student_dashboard_shows_outreach(self):
        self.client.login(username="student", password="password")
        url = reverse("profile_dashboard")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Dashboard Event")
        self.assertContains(resp, "Join")
        # Check link uses program ID
        self.assertContains(resp, f'href="/programs/{self.program.id}/outreach/"')

    def test_student_dashboard_shows_signed_up_badge(self):
        OutreachSignup.objects.create(student=self.student_profile, event=self.event, role=OutreachSignup.HELPER)
        self.client.login(username="student", password="password")
        url = reverse("profile_dashboard")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Dashboard Event")
        self.assertContains(resp, "Signed Up")

    def test_parent_dashboard_shows_outreach(self):
        self.client.login(username="parent", password="password")
        url = reverse("profile_dashboard")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Dashboard Event")
        self.assertContains(resp, "Available")

    def test_parent_dashboard_shows_going_badge(self):
        OutreachSignup.objects.create(student=self.student_profile, event=self.event, role=OutreachSignup.HELPER)
        self.client.login(username="parent", password="password")
        url = reverse("profile_dashboard")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Dashboard Event")
        self.assertContains(resp, "Going")

    def test_parent_nav_bar_hides_outreach(self):
        self.client.login(username="parent", password="password")
        url = reverse("profile_dashboard")
        resp = self.client.get(url)
        # Check that Outreach link is NOT in nav bar
        self.assertNotContains(resp, 'Outreach</a>')

    def test_student_nav_bar_shows_outreach_when_program_selected(self):
        self.client.login(username="student", password="password")
        # On dashboard, if student has 1 program, context processor sets it
        url = reverse("profile_dashboard")
        resp = self.client.get(url)
        self.assertContains(resp, f'href="/programs/{self.program.id}/outreach/">Outreach</a>')

    def test_dashboard_respects_outreach_feature_toggle(self):
        self.program.features.remove(self.outreach_feature)
        self.client.login(username="student", password="password")
        url = reverse("profile_dashboard")
        resp = self.client.get(url)
        self.assertNotContains(resp, "Dashboard Event")
        self.assertContains(resp, "Not available for this program.")
        self.assertNotContains(resp, 'Outreach</a>')
