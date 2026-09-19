"""Tests for the "Needs Support" KPI tile on the outreach event list.

For Mentors/Lead Mentors the leaderboard row shows how many *upcoming*
events still need at least one champion, at least one helper, and at least
one mentor, so understaffed outreach is easy to spot.
"""

from datetime import time, timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from outreach.models import OutreachMentorSignup, OutreachSignup
from outreach.tests.factories import create_outreach_event
from programs.models import Adult, Program, ProgramFeature, School, Student


def _upcoming():
    return timezone.now().date() + timedelta(days=30)


def _past():
    return timezone.now().date() - timedelta(days=30)


class EventListSupportKpisTest(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Test School")
        self.feature, _ = ProgramFeature.objects.get_or_create(
            key="outreach", defaults={"name": "Outreach"}
        )
        self.program = Program.objects.create(name="Test Program")
        self.program.features.add(self.feature)

        self.mentor_user = User.objects.create_user(
            username="mentor", password="password"  # nosec B106
        )
        Adult.objects.create(user=self.mentor_user, is_mentor=True, mentor_active=True)

        self.list_url = reverse("outreach:event_list", args=[self.program.id])

    def _make_event(
        self,
        *,
        name,
        start_date,
        max_champions=1,
        max_helpers=5,
        champions=0,
        helpers=0,
        mentors=0,
    ):
        event = create_outreach_event(
            program=self.program,
            name=name,
            location_name="Test Location",
            location_address="123 Test St",
            start_date=start_date,
            start_time=time(10, 0),
            end_time=time(12, 0),
        )
        shift = event.shifts.first()
        shift.max_champions = max_champions
        shift.max_helpers = max_helpers
        shift.save()

        for i in range(champions):
            student = Student.objects.create(
                legal_first_name=f"C{i}",
                last_name=name,
                school=self.school,
                graduation_year=2027,
            )
            OutreachSignup.objects.create(
                student=student, shift=shift, role=OutreachSignup.CHAMPION
            )
        for i in range(helpers):
            student = Student.objects.create(
                legal_first_name=f"H{i}",
                last_name=name,
                school=self.school,
                graduation_year=2027,
            )
            OutreachSignup.objects.create(
                student=student, shift=shift, role=OutreachSignup.HELPER
            )
        for i in range(mentors):
            adult = Adult.objects.create(
                legal_first_name=f"M{i}",
                last_name=name,
                is_mentor=True,
                mentor_active=True,
            )
            OutreachMentorSignup.objects.create(adult=adult, shift=shift)
        return event

    def get_kpis(self):
        self.client.login(username="mentor", password="password")  # nosec B106
        resp = self.client.get(self.list_url)
        self.assertEqual(resp.status_code, 200)
        return resp.context

    def test_counts_events_needing_each_role(self):
        # Fully staffed: 1/1 champions, 2/2 helpers, one mentor.
        self._make_event(
            name="Full",
            start_date=_upcoming(),
            max_champions=1,
            max_helpers=2,
            champions=1,
            helpers=2,
            mentors=1,
        )
        # Needs a champion (1/2) AND a helper (1/3); mentors are covered.
        self._make_event(
            name="Need Students",
            start_date=_upcoming(),
            max_champions=2,
            max_helpers=3,
            champions=1,
            helpers=1,
            mentors=2,
        )
        # Needs a mentor only.
        self._make_event(
            name="Need Mentors",
            start_date=_upcoming(),
            max_champions=1,
            max_helpers=1,
            champions=1,
            helpers=1,
            mentors=0,
        )

        context = self.get_kpis()
        self.assertEqual(context["needs_champions_events"], 1)
        self.assertEqual(context["needs_helpers_events"], 1)
        self.assertEqual(context["needs_mentors_events"], 1)

    def test_past_events_are_not_counted(self):
        self._make_event(
            name="Past Needy",
            start_date=_past(),
            max_champions=2,
            max_helpers=5,
            champions=0,
            helpers=0,
            mentors=0,
        )
        context = self.get_kpis()
        self.assertEqual(context["needs_champions_events"], 0)
        self.assertEqual(context["needs_helpers_events"], 0)
        self.assertEqual(context["needs_mentors_events"], 0)

    def test_zero_capacity_roles_are_not_needed(self):
        # max_champions=0 / max_helpers=0 means those roles aren't required.
        self._make_event(
            name="Zero Caps",
            start_date=_upcoming(),
            max_champions=0,
            max_helpers=0,
            champions=0,
            helpers=0,
            mentors=0,
        )
        context = self.get_kpis()
        self.assertEqual(context["needs_champions_events"], 0)
        self.assertEqual(context["needs_helpers_events"], 0)
        self.assertEqual(context["needs_mentors_events"], 1)

    def test_event_with_multiple_needy_shifts_counts_once_per_role(self):
        event = create_outreach_event(
            program=self.program,
            name="Two Shifts",
            location_name="Test Location",
            location_address="123 Test St",
            start_date=_upcoming(),
            start_time=time(10, 0),
            end_time=time(12, 0),
            end_date=_upcoming() + timedelta(days=1),
        )
        for shift in event.shifts.all():
            shift.max_champions = 2
            shift.max_helpers = 5
            shift.save()
        # Both shifts are empty, but the event still counts once per role.
        context = self.get_kpis()
        self.assertEqual(context["needs_champions_events"], 1)
        self.assertEqual(context["needs_helpers_events"], 1)
        self.assertEqual(context["needs_mentors_events"], 1)

    def test_kpi_tile_rendered_for_mentors(self):
        self.client.login(username="mentor", password="password")  # nosec B106
        resp = self.client.get(self.list_url)
        self.assertContains(resp, "Needs Support")
        self.assertContains(resp, "Champions")
        self.assertContains(resp, "Mentors")
