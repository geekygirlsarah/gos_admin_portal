from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from outreach.models import OutreachEvent, OutreachSignup
from outreach.tests.factories import create_outreach_event
from programs.models import Adult, Enrollment, Program, ProgramFeature, School, Student


class OutreachDashboardTest(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Test School")
        self.program = Program.objects.create(name="Test Program", active=True)
        self.outreach_feature, _ = ProgramFeature.objects.get_or_create(
            key="outreach", defaults={"name": "Outreach"}
        )
        self.program.features.add(self.outreach_feature)

        # Student
        self.student_user = User.objects.create_user(
            username="student", password="password"
        )  # nosec B106
        self.student_profile = Student.objects.create(
            user=self.student_user,
            legal_first_name="Test",
            last_name="Student",
            school=self.school,
            graduation_year=2027,
        )
        self.enrollment = Enrollment.objects.create(
            student=self.student_profile, program=self.program, active=True
        )

        # Parent
        self.parent_user = User.objects.create_user(
            username="parent", password="password"
        )  # nosec B106
        self.parent_adult = Adult.objects.create(user=self.parent_user, is_parent=True)
        self.parent_adult.students.add(self.student_profile)

        self.event = create_outreach_event(
            program=self.program,
            name="Dashboard Event",
            location_name="Loc",
            location_address="Addr",
            start_date=timezone.now().date(),
            start_time="10:00:00",
            end_time="12:00:00",
        )
        self.shift = self.event.shifts.first()
        self.shift.max_champions = 1
        self.shift.max_helpers = 5
        self.shift.save()

    def test_student_dashboard_shows_outreach_stats(self):
        self.client.login(username="student", password="password")  # nosec B106
        url = reverse("profile_dashboard")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "events championed")
        self.assertContains(resp, "hours completed")
        self.assertContains(resp, "View Outreach Events")
        # Check link uses program ID
        self.assertContains(resp, f'href="/programs/{self.program.id}/outreach/"')

    def test_student_dashboard_shows_championed_count(self):
        OutreachSignup.objects.create(
            student=self.student_profile,
            shift=self.shift,
            role=OutreachSignup.CHAMPION,
        )
        self.client.login(username="student", password="password")  # nosec B106
        url = reverse("profile_dashboard")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "<strong>1</strong> events championed", html=True)

    def test_parent_dashboard_shows_outreach(self):
        self.client.login(username="parent", password="password")  # nosec B106
        url = reverse("profile_dashboard")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Dashboard Event")
        self.assertContains(resp, "Available")

    def test_parent_dashboard_shows_going_badge(self):
        OutreachSignup.objects.create(
            student=self.student_profile, shift=self.shift, role=OutreachSignup.HELPER
        )
        self.client.login(username="parent", password="password")  # nosec B106
        url = reverse("profile_dashboard")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Dashboard Event")
        self.assertContains(resp, "Going")

    def test_parent_dashboard_shows_all_signed_up_events(self):
        """Events a child is signed up for should never be capped."""
        from datetime import timedelta

        event_b = create_outreach_event(
            program=self.program,
            name="Event B",
            location_name="Loc",
            location_address="Addr",
            start_date=timezone.now().date() + timedelta(days=1),
            start_time="10:00:00",
            end_time="12:00:00",
        )
        event_c = create_outreach_event(
            program=self.program,
            name="Event C",
            location_name="Loc",
            location_address="Addr",
            start_date=timezone.now().date() + timedelta(days=2),
            start_time="10:00:00",
            end_time="12:00:00",
        )
        for event in [self.event, event_b, event_c]:
            OutreachSignup.objects.create(
                student=self.student_profile,
                shift=event.shifts.first(),
                role=OutreachSignup.HELPER,
            )

        self.client.login(username="parent", password="password")  # nosec B106
        url = reverse("profile_dashboard")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Dashboard Event")
        self.assertContains(resp, "Event B")
        self.assertContains(resp, "Event C")

    def test_parent_dashboard_limits_suggested_events_to_two(self):
        """Events a child has not signed up for should be capped at two."""
        from datetime import timedelta

        create_outreach_event(
            program=self.program,
            name="Event B",
            location_name="Loc",
            location_address="Addr",
            start_date=timezone.now().date() + timedelta(days=1),
            start_time="10:00:00",
            end_time="12:00:00",
        )
        create_outreach_event(
            program=self.program,
            name="Event C",
            location_name="Loc",
            location_address="Addr",
            start_date=timezone.now().date() + timedelta(days=2),
            start_time="10:00:00",
            end_time="12:00:00",
        )
        create_outreach_event(
            program=self.program,
            name="Event D",
            location_name="Loc",
            location_address="Addr",
            start_date=timezone.now().date() + timedelta(days=3),
            start_time="10:00:00",
            end_time="12:00:00",
        )

        self.client.login(username="parent", password="password")  # nosec B106
        url = reverse("profile_dashboard")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        # "Dashboard Event" and "Event B" are the two earliest, not-signed-up events
        self.assertContains(resp, "Dashboard Event")
        self.assertContains(resp, "Event B")
        self.assertNotContains(resp, "Event C")
        self.assertNotContains(resp, "Event D")

    def test_parent_nav_bar_hides_outreach(self):
        self.client.login(username="parent", password="password")  # nosec B106
        url = reverse("profile_dashboard")
        resp = self.client.get(url)
        # Check that Outreach link is NOT in nav bar
        self.assertNotContains(resp, "Outreach</a>")

    def test_student_nav_bar_shows_outreach_when_program_selected(self):
        self.client.login(username="student", password="password")  # nosec B106
        # On dashboard, if student has 1 program, context processor sets it
        url = reverse("profile_dashboard")
        resp = self.client.get(url)
        self.assertContains(
            resp, f'href="/programs/{self.program.id}/outreach/">Outreach</a>'
        )

    def test_dashboard_respects_outreach_feature_toggle(self):
        self.program.features.remove(self.outreach_feature)
        self.client.login(username="student", password="password")  # nosec B106
        url = reverse("profile_dashboard")
        resp = self.client.get(url)
        self.assertNotContains(resp, "Dashboard Event")
        self.assertContains(resp, "Not available for this program.")
        self.assertNotContains(resp, "Outreach</a>")
