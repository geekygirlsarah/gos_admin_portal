from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from outreach.models import OutreachEvent, OutreachSignup
from outreach.tests.factories import create_outreach_event
from programs.models import Program, ProgramFeature, School, Student


class OutreachEventListTest(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Test School")
        self.feature, _ = ProgramFeature.objects.get_or_create(
            key="outreach", defaults={"name": "Outreach"}
        )
        self.program = Program.objects.create(name="Test Program")
        self.program.features.add(self.feature)

        self.user = User.objects.create_user(
            username="student", password="password"
        )  # nosec B106
        self.student = Student.objects.create(
            user=self.user,
            legal_first_name="Test",
            last_name="Student",
            school=self.school,
            graduation_year=2027,
        )

        today = timezone.now().date()

        # Upcoming event
        self.upcoming = create_outreach_event(
            program=self.program,
            name="Upcoming Event",
            location_name="Future Loc",
            location_address="123 Future St",
            start_date=today + timedelta(days=1),
            start_time="10:00:00",
            end_time="12:00:00",
        )

        # Past event
        self.past = create_outreach_event(
            program=self.program,
            name="Past Event",
            location_name="Old Loc",
            location_address="456 History Ave",
            start_date=today - timedelta(days=5),
            start_time="10:00:00",
            end_time="12:00:00",
        )

        # Multi-day event that is currently active (should be upcoming)
        self.active = create_outreach_event(
            program=self.program,
            name="Active Event",
            location_name="Current Loc",
            location_address="789 Now Rd",
            start_date=today - timedelta(days=1),
            end_date=today + timedelta(days=1),
            start_time="10:00:00",
            end_time="12:00:00",
        )

    def test_event_list_separation(self):
        self.client.login(username="student", password="password")  # nosec B106
        url = reverse("outreach:event_list", args=[self.program.id])
        resp = self.client.get(url)

        self.assertEqual(resp.status_code, 200)

        # Check upcoming events are in context
        upcoming_names = [e.name for e in resp.context["upcoming_events"]]
        self.assertIn("Upcoming Event", upcoming_names)
        self.assertIn("Active Event", upcoming_names)
        self.assertNotIn("Past Event", upcoming_names)

        # Check past events are in context
        past_names = [e.name for e in resp.context["past_events"]]
        self.assertIn("Past Event", past_names)
        self.assertNotIn("Upcoming Event", past_names)
        self.assertNotIn("Active Event", past_names)

    def test_address_and_map_link_present(self):
        self.client.login(username="student", password="password")  # nosec B106
        url = reverse("outreach:event_list", args=[self.program.id])
        resp = self.client.get(url)

        self.assertContains(resp, "123 Future St")
        # Django's urlencode filter uses %20 for spaces
        self.assertContains(
            resp, "google.com/maps/search/?api=1&query=123%20Future%20St"
        )

    def test_accordion_present_when_past_events_exist(self):
        self.client.login(username="student", password="password")  # nosec B106
        url = reverse("outreach:event_list", args=[self.program.id])
        resp = self.client.get(url)

        self.assertContains(resp, 'id="pastEventsAccordion"')
        self.assertContains(resp, "Past Events (1)")

    def test_accordion_absent_when_no_past_events(self):
        OutreachEvent.objects.filter(name="Past Event").delete()
        self.client.login(username="student", password="password")  # nosec B106
        url = reverse("outreach:event_list", args=[self.program.id])
        resp = self.client.get(url)

        self.assertNotContains(resp, 'id="pastEventsAccordion"')

    def test_student_past_events_sorting_and_participated_badge(self):
        today = timezone.now().date()
        # Create additional past events
        past_participated_recent = create_outreach_event(
            program=self.program,
            name="Participated Recent",
            location_name="Loc A",
            location_address="123 A St",
            start_date=today - timedelta(days=5),
            start_time="10:00:00",
            end_time="12:00:00",
        )
        past_participated_older = create_outreach_event(
            program=self.program,
            name="Participated Older",
            location_name="Loc B",
            location_address="456 B St",
            start_date=today - timedelta(days=15),
            start_time="10:00:00",
            end_time="12:00:00",
        )
        past_not_participated_newest = create_outreach_event(
            program=self.program,
            name="Not Participated Newest",
            location_name="Loc C",
            location_address="789 C St",
            start_date=today - timedelta(days=2),
            start_time="10:00:00",
            end_time="12:00:00",
        )

        # Student signs up for Participated Recent and Participated Older
        OutreachSignup.objects.create(
            shift=past_participated_recent.shifts.first(),
            student=self.student,
            role=OutreachSignup.HELPER,
        )
        OutreachSignup.objects.create(
            shift=past_participated_older.shifts.first(),
            student=self.student,
            role=OutreachSignup.CHAMPION,
        )

        self.client.login(username="student", password="password")  # nosec B106
        url = reverse("outreach:event_list", args=[self.program.id])
        resp = self.client.get(url)

        self.assertEqual(resp.status_code, 200)

        # Check order of past events in context: participated ones up front (newest to oldest), then non-participated (newest to oldest)
        past_names = [e.name for e in resp.context["past_events"]]
        self.assertEqual(
            past_names,
            [
                "Participated Recent",
                "Participated Older",
                "Not Participated Newest",
                "Past Event",
            ],
        )

        # Check that Participated badge is rendered for participated past events
        self.assertContains(resp, "Participated")
        content = resp.content.decode("utf-8")
        # Ensure badge is attached to participated events and not non-participated
        self.assertIn("Participated Recent", content)
        self.assertIn("Participated Older", content)

    def test_mentor_past_events_order_and_no_participated_badge(self):
        today = timezone.now().date()
        past_recent = create_outreach_event(
            program=self.program,
            name="Past Recent",
            location_name="Loc A",
            location_address="123 A St",
            start_date=today - timedelta(days=2),
            start_time="10:00:00",
            end_time="12:00:00",
        )
        past_older = create_outreach_event(
            program=self.program,
            name="Past Older",
            location_name="Loc B",
            location_address="456 B St",
            start_date=today - timedelta(days=10),
            start_time="10:00:00",
            end_time="12:00:00",
        )
        mentor_user = User.objects.create_user(
            username="mentor", password="password"
        )  # nosec B106
        from programs.models import Adult

        Adult.objects.create(user=mentor_user, is_mentor=True, mentor_active=True)

        self.client.login(username="mentor", password="password")  # nosec B106
        url = reverse("outreach:event_list", args=[self.program.id])
        resp = self.client.get(url)

        self.assertEqual(resp.status_code, 200)
        past_names = [e.name for e in resp.context["past_events"]]
        self.assertEqual(
            past_names,
            ["Past Recent", "Past Event", "Past Older"],
        )
        self.assertNotContains(resp, "Participated</span>")
