from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from outreach.models import OutreachEvent, OutreachSignup
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
        self.upcoming = OutreachEvent.objects.create(
            program=self.program,
            name="Upcoming Event",
            location_name="Future Loc",
            location_address="123 Future St",
            start_date=today + timedelta(days=1),
            start_time="10:00:00",
            end_time="12:00:00",
        )

        # Past event
        self.past = OutreachEvent.objects.create(
            program=self.program,
            name="Past Event",
            location_name="Old Loc",
            location_address="456 History Ave",
            start_date=today - timedelta(days=5),
            start_time="10:00:00",
            end_time="12:00:00",
        )

        # Multi-day event that is currently active (should be upcoming)
        self.active = OutreachEvent.objects.create(
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
