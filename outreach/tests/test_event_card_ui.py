"""Event card UI: collapsed shift schedule, tap-for-full description,
clickable location + copyable address, and the per-shift check-in button.
"""

from datetime import time, timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from outreach.models import OutreachSignup
from outreach.tests.factories import create_outreach_event
from programs.models import Adult, Program, ProgramFeature, School, Student


class EventCardUIBase(TestCase):
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
            username="student", password="password"
        )  # nosec B106
        self.student = Student.objects.create(
            user=self.student_user,
            legal_first_name="Test",
            last_name="Student",
            school=self.school,
            graduation_year=2027,
        )

        tomorrow = timezone.now().date() + timedelta(days=1)
        self.upcoming = create_outreach_event(
            program=self.program,
            name="Upcoming Event",
            location_name="Future Loc",
            location_address="123 Future St",
            description="Short description.",
            start_date=tomorrow,
            start_time=time(10, 0),
            end_time=time(12, 0),
        )
        self.shift = self.upcoming.shifts.first()
        self.shift.max_champions = 1
        self.shift.save()

        self.list_url = reverse("outreach:event_list", args=[self.program.id])


class EventCardAccordionTests(EventCardUIBase):
    def test_shift_accordion_summary_present(self):
        self.client.login(username="student", password="password")  # nosec B106
        resp = self.client.get(self.list_url)
        self.assertContains(resp, "1 shift available")
        self.assertContains(resp, f'data-bs-target="#collapseShifts{self.upcoming.pk}"')

    def test_shift_accordion_summary_shows_schedule(self):
        self.client.login(username="student", password="password")  # nosec B106
        resp = self.client.get(self.list_url)
        self.assertContains(resp, "10:00 AM - 12:00 PM")


class EventCardDescriptionTests(EventCardUIBase):
    def test_short_description_rendered_in_full(self):
        self.client.login(username="student", password="password")  # nosec B106
        resp = self.client.get(self.list_url)
        self.assertContains(resp, "Short description.")
        # No tappable toggle span for short descriptions (the JS references
        # the class, so assert against the rendered span itself).
        self.assertNotContains(resp, 'class="description-toggle')

    def test_long_description_truncated_with_modal_trigger(self):
        long = " ".join(f"word{i}" for i in range(40))
        self.upcoming.description = long
        self.upcoming.save()
        self.client.login(username="student", password="password")  # nosec B106
        resp = self.client.get(self.list_url)
        self.assertContains(resp, 'class="description-toggle')
        self.assertContains(resp, 'data-description="' + long + '"')


class EventCardLocationTests(EventCardUIBase):
    def test_location_name_is_link_to_maps(self):
        self.client.login(username="student", password="password")  # nosec B106
        resp = self.client.get(self.list_url)
        self.assertContains(
            resp, "google.com/maps/search/?api=1&query=123%20Future%20St"
        )
        self.assertContains(resp, ">Future Loc</a>")

    def test_address_has_copy_button(self):
        self.client.login(username="student", password="password")  # nosec B106
        resp = self.client.get(self.list_url)
        self.assertContains(resp, "address-copy-btn")
        self.assertContains(resp, 'data-copy="123 Future St"')


class EventCardCheckInButtonTests(EventCardUIBase):
    def test_check_in_button_shown_to_mentor(self):
        self.client.login(username="mentor", password="password")  # nosec B106
        resp = self.client.get(self.list_url)
        self.assertContains(
            resp,
            reverse("outreach:shift_check_in", args=[self.program.id, self.shift.pk]),
        )

    def test_check_in_button_shown_to_champion_of_shift(self):
        OutreachSignup.objects.create(
            shift=self.shift, student=self.student, role=OutreachSignup.CHAMPION
        )
        self.client.login(username="student", password="password")  # nosec B106
        resp = self.client.get(self.list_url)
        self.assertContains(
            resp,
            reverse("outreach:shift_check_in", args=[self.program.id, self.shift.pk]),
        )

    def test_check_in_button_hidden_for_non_champion_student(self):
        self.client.login(username="student", password="password")  # nosec B106
        resp = self.client.get(self.list_url)
        self.assertNotContains(
            resp,
            reverse("outreach:shift_check_in", args=[self.program.id, self.shift.pk]),
        )

    def test_check_in_button_hidden_for_champion_after_shift_finalized(self):
        """Once everyone on a shift has checked out, the champion's
        check-in button disappears (mentors still have it)."""
        signup = OutreachSignup.objects.create(
            shift=self.shift, student=self.student, role=OutreachSignup.CHAMPION
        )
        # Mark the shift as already over with full attendance stamped.
        self.shift.date = timezone.now().date() - timedelta(days=1)
        self.shift.save()
        signup.checked_in_at = timezone.now() - timedelta(hours=3)
        signup.checked_out_at = timezone.now() - timedelta(hours=1)
        signup.save(update_fields=["checked_in_at", "checked_out_at"])

        url = reverse("outreach:shift_check_in", args=[self.program.id, self.shift.pk])
        self.client.login(username="student", password="password")  # nosec B106
        resp = self.client.get(self.list_url)
        self.assertNotContains(resp, url)

        self.client.login(username="mentor", password="password")  # nosec B106
        resp = self.client.get(self.list_url)
        self.assertContains(resp, url)
