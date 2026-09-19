"""Tests for Lead Mentor management of mentor support signups.

Mentors can manage which *students* are signed up to a shift, but only
Lead Mentors can edit which *mentors* are supporting a shift (the
``shift_manage_mentor_signups`` view and ``OutreachManageMentorSignupsForm``).
"""

from datetime import date, time, timedelta

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from django.utils.formats import localize

from outreach.forms import OutreachManageMentorSignupsForm
from outreach.models import OutreachMentorSignup, OutreachShift
from outreach.tests.factories import create_outreach_event
from programs.models import Adult, Program, ProgramFeature, School


def _next_year_month_day():
    return date.today() + timedelta(days=180)


class OutreachManageMentorSignupsViewTest(TestCase):
    def setUp(self):
        self.lead_mentor_group, _ = Group.objects.get_or_create(name="LeadMentor")

        self.school = School.objects.create(name="Test School")
        self.feature, _ = ProgramFeature.objects.get_or_create(
            key="outreach", defaults={"name": "Outreach"}
        )
        self.program = Program.objects.create(name="Test Program")
        self.program.features.add(self.feature)

        self.lead_user = User.objects.create_user(
            username="lead", password="password"  # nosec B106
        )
        self.lead_user.groups.add(self.lead_mentor_group)
        self.lead_adult = Adult.objects.create(
            user=self.lead_user,
            legal_first_name="Laura",
            last_name="Lead",
            is_mentor=True,
            mentor_active=True,
        )

        self.mentor_user = User.objects.create_user(
            username="mentor", password="password"  # nosec B106
        )
        self.mentor_adult = Adult.objects.create(
            user=self.mentor_user,
            legal_first_name="Molly",
            last_name="Mentor",
            is_mentor=True,
            mentor_active=True,
        )

        self.mentor2_user = User.objects.create_user(
            username="mentor2", password="password"  # nosec B106
        )
        self.mentor2_adult = Adult.objects.create(
            user=self.mentor2_user,
            legal_first_name="Olive",
            last_name="Other",
            is_mentor=True,
            mentor_active=True,
        )

        self.event = create_outreach_event(
            program=self.program,
            name="Test Event",
            location_name="Test Location",
            location_address="123 Test St",
            start_date=_next_year_month_day(),
            start_time=time(10, 0),
            end_time=time(12, 0),
        )
        self.shift = self.event.shifts.first()
        self.manage_url = reverse(
            "outreach:shift_manage_mentor_signups",
            args=[self.program.id, self.shift.pk],
        )
        self.list_url = reverse("outreach:event_list", args=[self.program.id])

    def test_lead_mentor_can_access_manage_view(self):
        self.client.login(username="lead", password="password")  # nosec B106
        resp = self.client.get(self.manage_url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Supporting Mentors")

    def test_manage_dialog_shows_friendly_date(self):
        self.client.login(username="lead", password="password")  # nosec B106
        resp = self.client.get(self.manage_url)
        html = resp.content.decode()
        self.assertIn(f"({localize(self.shift.date)})", html)
        self.assertNotIn(str(self.shift.date), html)

    def test_success_message_shows_friendly_date(self):
        self.client.login(username="lead", password="password")  # nosec B106
        resp = self.client.post(
            self.manage_url, {"mentors": [self.mentor_adult.pk]}, follow=True
        )
        html = resp.content.decode()
        self.assertIn(localize(self.shift.date), html)
        self.assertNotIn(str(self.shift.date), html)

    def test_regular_mentor_is_denied(self):
        self.client.login(username="mentor", password="password")  # nosec B106
        resp = self.client.get(self.manage_url)
        self.assertEqual(resp.status_code, 302)
        self.assertNotEqual(resp.url, self.manage_url)

    def test_student_is_denied(self):
        student_user = User.objects.create_user(
            username="student", password="password"  # nosec B106
        )
        from programs.models import Enrollment, Student

        student = Student.objects.create(
            user=student_user,
            legal_first_name="S",
            last_name="T",
            school=self.school,
            graduation_year=2027,
        )
        Enrollment.objects.create(student=student, program=self.program, active=True)
        self.client.login(username="student", password="password")  # nosec B106
        resp = self.client.get(self.manage_url)
        self.assertEqual(resp.status_code, 302)
        self.assertNotEqual(resp.url, self.manage_url)

    def test_anonymous_redirected_to_login(self):
        resp = self.client.get(self.manage_url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("login", resp.url)

    def test_lead_mentor_can_add_mentors(self):
        self.client.login(username="lead", password="password")  # nosec B106
        resp = self.client.post(
            self.manage_url, {"mentors": [self.mentor_adult.pk, self.mentor2_adult.pk]}
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self.shift.mentor_signups.count(), 2)
        self.assertTrue(
            OutreachMentorSignup.objects.filter(
                adult=self.mentor_adult, shift=self.shift
            ).exists()
        )
        self.assertTrue(
            OutreachMentorSignup.objects.filter(
                adult=self.mentor2_adult, shift=self.shift
            ).exists()
        )

    def test_lead_mentor_can_remove_mentors(self):
        OutreachMentorSignup.objects.create(adult=self.mentor_adult, shift=self.shift)
        OutreachMentorSignup.objects.create(adult=self.mentor2_adult, shift=self.shift)
        self.client.login(username="lead", password="password")  # nosec B106
        resp = self.client.post(self.manage_url, {"mentors": [self.mentor_adult.pk]})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self.shift.mentor_signups.count(), 1)
        self.assertFalse(
            OutreachMentorSignup.objects.filter(
                adult=self.mentor2_adult, shift=self.shift
            ).exists()
        )

    def test_submitting_mentor_twice_is_idempotent(self):
        self.client.login(username="lead", password="password")  # nosec B106
        self.client.post(self.manage_url, {"mentors": [self.mentor_adult.pk]})
        self.client.post(self.manage_url, {"mentors": [self.mentor_adult.pk]})
        self.assertEqual(
            OutreachMentorSignup.objects.filter(
                adult=self.mentor_adult, shift=self.shift
            ).count(),
            1,
        )

    def test_regular_mentor_cannot_post(self):
        OutreachMentorSignup.objects.create(adult=self.mentor_adult, shift=self.shift)
        self.client.login(username="mentor", password="password")  # nosec B106
        resp = self.client.post(self.manage_url, {"mentors": []})
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(
            OutreachMentorSignup.objects.filter(
                adult=self.mentor_adult, shift=self.shift
            ).exists()
        )

    def test_event_list_shows_manage_button_to_lead_mentor_only(self):
        self.client.login(username="lead", password="password")  # nosec B106
        resp = self.client.get(self.list_url)
        self.assertContains(resp, self.manage_url)

        self.client.logout()
        self.client.login(username="mentor", password="password")  # nosec B106
        resp = self.client.get(self.list_url)
        self.assertNotContains(resp, self.manage_url)


class MentorSignupDuplicatePickerTest(TestCase):
    """A mentor supporting several shifts is never shown more than once."""

    def setUp(self):
        self.lead_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.feature, _ = ProgramFeature.objects.get_or_create(
            key="outreach", defaults={"name": "Outreach"}
        )
        self.program = Program.objects.create(name="Test Program")
        self.program.features.add(self.feature)

        self.lead_user = User.objects.create_user(
            username="lead", password="password"  # nosec B106
        )
        self.lead_user.groups.add(self.lead_group)

        self.event = create_outreach_event(
            program=self.program,
            name="Test Event",
            location_name="Test Location",
            location_address="123 Test St",
            start_date=_next_year_month_day(),
            start_time=time(10, 0),
            end_time=time(12, 0),
        )
        self.first_shift = self.event.shifts.first()
        self.second_shift = OutreachShift.objects.create(
            event=self.event,
            date=_next_year_month_day(),
            start_time=time(13, 0),
            end_time=time(15, 0),
        )

        # A mentor who supports BOTH shifts of the event.
        self.mentor_adult = Adult.objects.create(
            legal_first_name="Molly",
            last_name="Mentor",
            is_mentor=True,
            mentor_active=True,
        )
        self.shift1_signup = OutreachMentorSignup.objects.create(
            adult=self.mentor_adult, shift=self.first_shift
        )
        self.shift2_signup = OutreachMentorSignup.objects.create(
            adult=self.mentor_adult, shift=self.second_shift
        )

        self.manage_url = reverse(
            "outreach:shift_manage_mentor_signups",
            args=[self.program.id, self.first_shift.pk],
        )

    def test_picker_queryset_lists_mentor_once(self):
        form = OutreachManageMentorSignupsForm(shift=self.first_shift)
        queryset = form.fields["mentors"].queryset
        pks = [adult.pk for adult in queryset]
        self.assertEqual(pks.count(self.mentor_adult.pk), 1)

    def test_dialog_renders_mentor_once(self):
        self.client.login(username="lead", password="password")  # nosec B106
        resp = self.client.get(self.manage_url)
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertEqual(html.count(self.mentor_adult.display_name), 1)
