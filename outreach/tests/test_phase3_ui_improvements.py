from datetime import date, time, timedelta

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from outreach.models import OutreachEvent, OutreachShift, OutreachSignup
from outreach.tests.factories import create_outreach_event
from programs.models import Adult, Program, ProgramFeature, School, Student


class Phase3OutreachUITests(TestCase):
    def setUp(self):
        self.lead_mentor_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.mentor_group, _ = Group.objects.get_or_create(name="Mentor")

        self.school = School.objects.create(name="Test High")
        self.feat_outreach, _ = ProgramFeature.objects.get_or_create(
            key="outreach", defaults={"name": "Outreach"}
        )
        self.program = Program.objects.create(name="FRC 2026-2027", active=True)
        self.program.features.add(self.feat_outreach)

        self.mentor_user = User.objects.create_user(
            username="outreach_mentor", password="password123"  # nosec B106
        )
        self.mentor_user.groups.add(self.mentor_group)
        self.mentor_adult = Adult.objects.create(
            user=self.mentor_user, is_mentor=True, mentor_active=True
        )

        self.student_user = User.objects.create_user(
            username="outreach_student", password="password123"  # nosec B106
        )
        self.student = Student.objects.create(
            user=self.student_user,
            legal_first_name="Maya",
            preferred_first_name="Maya",
            last_name="Lin",
            school=self.school,
            graduation_year=2028,
        )

        tomorrow = timezone.now().date() + timedelta(days=2)
        self.event = create_outreach_event(
            program=self.program,
            name="STEM Festival",
            location_name="Carnegie Science Center",
            location_address="1 Allegheny Ave, Pittsburgh, PA",
            description="Engage middle school students with interactive robotics demonstrations.",
            start_date=tomorrow,
            start_time=time(10, 0),
            end_time=time(14, 0),
        )
        self.shift = self.event.shifts.first()
        self.shift.max_champions = 2
        self.shift.max_helpers = 4
        self.shift.save()

    def test_student_event_list_stats_kpis_and_event_card(self):
        self.client.login(
            username="outreach_student", password="password123"
        )  # nosec B106
        resp = self.client.get(reverse("outreach:event_list", args=[self.program.id]))
        self.assertEqual(resp.status_code, 200)

        # Student KPI cards
        self.assertContains(resp, "Events Championed")
        self.assertContains(resp, "Completed Hours")
        self.assertContains(resp, "Pending Hours")

        # Event card details
        self.assertContains(resp, "STEM Festival")
        self.assertContains(resp, "Carnegie Science Center")
        self.assertContains(resp, "address-copy-btn")
        self.assertContains(resp, f'id="shiftAccordionBtn{self.event.pk}"')

    def test_mentor_event_list_summary_and_stats_button(self):
        self.client.login(
            username="outreach_mentor", password="password123"
        )  # nosec B106
        resp = self.client.get(reverse("outreach:event_list", args=[self.program.id]))
        self.assertEqual(resp.status_code, 200)

        # Mentor action buttons
        self.assertContains(resp, 'id="studentStatsBtn"')
        self.assertContains(resp, "Add Event")
