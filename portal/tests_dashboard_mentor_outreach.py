import datetime

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from outreach.models import OutreachMentorSignup
from outreach.tests.factories import create_outreach_event
from programs.models import Adult, Program, ProgramFeature


class MentorDashboardOutreachTests(TestCase):
    """The mentor dashboard lists upcoming outreach shifts the mentor supports."""

    def setUp(self):
        self.feature, _ = ProgramFeature.objects.get_or_create(
            key="outreach", defaults={"name": "Outreach"}
        )
        self.program = Program.objects.create(name="Test Program", active=True)
        self.program.features.add(self.feature)

        self.mentor_user = User.objects.create_user(
            username="mentor", password="password"
        )  # nosec B106
        self.mentor = Adult.objects.create(
            user=self.mentor_user,
            legal_first_name="Molly",
            last_name="Mentor",
            is_mentor=True,
        )

        today = datetime.date.today()
        self.upcoming_event = create_outreach_event(
            program=self.program,
            name="Future Expo",
            location_name="Science Center",
            location_address="1 Science St",
            start_date=today + datetime.timedelta(days=14),
            start_time=datetime.time(10, 0),
            end_time=datetime.time(12, 0),
        )
        self.past_event = create_outreach_event(
            program=self.program,
            name="Past Expo",
            location_name="Old Hall",
            location_address="2 Old St",
            start_date=today - datetime.timedelta(days=14),
            start_time=datetime.time(10, 0),
            end_time=datetime.time(12, 0),
        )

    def _signup(self, event):
        return OutreachMentorSignup.objects.create(
            adult=self.mentor, shift=event.shifts.first()
        )

    def test_shows_upcoming_support_signups(self):
        signup = self._signup(self.upcoming_event)
        self.client.login(username="mentor", password="password")  # nosec B106
        resp = self.client.get(reverse("profile_dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "My Outreach Signups")
        self.assertContains(resp, "Future Expo")
        self.assertContains(
            resp,
            reverse("outreach:event_list", args=[self.program.id]),
        )
        # The shift's own cancel URL appears so mentors can back out quickly.
        self.assertContains(
            resp,
            reverse(
                "outreach:shift_mentor_cancel", args=[self.program.id, signup.shift_id]
            ),
        )

    def test_hides_past_signups(self):
        self._signup(self.past_event)
        self.client.login(username="mentor", password="password")  # nosec B106
        resp = self.client.get(reverse("profile_dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Past Expo")

    def test_empty_state_when_no_signups(self):
        self.client.login(username="mentor", password="password")  # nosec B106
        resp = self.client.get(reverse("profile_dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "My Outreach Signups")
        self.assertContains(resp, "not signed up to support any upcoming")

    def test_section_hidden_for_non_mentors(self):
        parent_user = User.objects.create_user(
            username="parent", password="password"
        )  # nosec B106
        Adult.objects.create(
            user=parent_user,
            legal_first_name="Paula",
            last_name="Parent",
            is_parent=True,
        )
        self.client.login(username="parent", password="password")  # nosec B106
        resp = self.client.get(reverse("profile_dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "My Outreach Signups")

    def test_signups_ordered_by_shift_date(self):
        far_event = create_outreach_event(
            program=self.program,
            name="Far Event",
            location_name="Loc",
            location_address="Addr",
            start_date=datetime.date.today() + datetime.timedelta(days=30),
            start_time=datetime.time(10, 0),
            end_time=datetime.time(12, 0),
        )
        first_signup = self._signup(self.upcoming_event)
        second_signup = self._signup(far_event)

        self.client.login(username="mentor", password="password")  # nosec B106
        resp = self.client.get(reverse("profile_dashboard"))
        content = resp.content.decode()
        self.assertLess(content.index("Future Expo"), content.index("Far Event"))

        # Sanity-check the fixtures actually differ in date so the assertion
        # above is meaningful.
        self.assertNotEqual(first_signup.shift.date, second_signup.shift.date)
