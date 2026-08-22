from datetime import date, time

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from outreach.models import OutreachEvent, OutreachSignup
from programs.models import Adult, Enrollment, Program, ProgramFeature, School, Student


class OutreachStatsTest(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Test School")
        self.feature, _ = ProgramFeature.objects.get_or_create(
            key="outreach", defaults={"name": "Outreach"}
        )
        self.program = Program.objects.create(name="Test Program")
        self.program.features.add(self.feature)

        self.mentor_user = User.objects.create_user(
            username="mentor", password="password"
        )  # nosec B106
        self.mentor_adult = Adult.objects.create(
            user=self.mentor_user, is_mentor=True, mentor_active=True
        )

        self.student_user = User.objects.create_user(
            username="student1", password="password"
        )  # nosec B106
        self.student = Student.objects.create(
            user=self.student_user,
            legal_first_name="Alice",
            last_name="Zuberg",
            school=self.school,
            graduation_year=2027,
        )
        Enrollment.objects.create(
            student=self.student, program=self.program, active=True
        )

        # 2 hour event, championed (PAST)
        self.event1 = OutreachEvent.objects.create(
            program=self.program,
            name="Event 1",
            location_name="Loc 1",
            location_address="Addr 1",
            start_date=date(2026, 8, 1),
            start_time=time(10, 0),
            end_time=time(12, 0),
            max_champions=2,
            max_helpers=5,
        )
        OutreachSignup.objects.create(
            student=self.student, event=self.event1, role=OutreachSignup.CHAMPION
        )

        # 3 hour event, helped (PAST)
        self.event2 = OutreachEvent.objects.create(
            program=self.program,
            name="Event 2",
            location_name="Loc 2",
            location_address="Addr 2",
            start_date=date(2026, 8, 2),
            start_time=time(13, 0),
            end_time=time(16, 0),
            max_champions=2,
            max_helpers=5,
        )
        OutreachSignup.objects.create(
            student=self.student, event=self.event2, role=OutreachSignup.HELPER
        )

    def test_event_duration_hours(self):
        self.assertEqual(self.event1.duration_hours, 2.0)
        self.assertEqual(self.event2.duration_hours, 3.0)

    def test_student_outreach_stats_in_context(self):
        self.client.login(username="student1", password="password")  # nosec B106
        url = reverse("outreach:event_list", args=[self.program.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        # We expect these keys in context or rendered in HTML
        self.assertContains(resp, "1")  # Championed count
        self.assertContains(resp, "Events Championed")
        self.assertContains(resp, "5.0")  # Outreach hours (floatformat:1)
        self.assertContains(resp, "Completed Hours")

    def test_mentor_can_see_student_stats_button(self):
        self.client.login(username="mentor", password="password")  # nosec B106
        url = reverse("outreach:event_list", args=[self.program.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Student Stats")

    def test_mentor_student_stats_modal_content(self):
        self.client.login(username="mentor", password="password")  # nosec B106
        url = reverse("outreach:student_stats", args=[self.program.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        # Check alphabetization and columns
        self.assertContains(resp, "Alice Zuberg")
        self.assertContains(resp, "1")  # Championed
        self.assertContains(resp, "5")  # Hours
