"""Students (and champions) are view-only once a shift has ended.

Mentors/Lead Mentors keep full control over rosters and check-in data so
they can correct mistakes after the fact.
"""

from datetime import time, timedelta

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from outreach.models import OutreachMentorSignup, OutreachSignup
from outreach.tests.factories import create_outreach_event
from programs.models import (
    Adult,
    Enrollment,
    Program,
    ProgramFeature,
    School,
    Student,
)


class PastShiftGatingBase(TestCase):
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
        Enrollment.objects.create(
            student=self.student, program=self.program, active=True
        )

        today = timezone.now().date()
        self.past_event = create_outreach_event(
            program=self.program,
            name="Past Event",
            location_name="Old Loc",
            location_address="456 History Ave",
            start_date=today - timedelta(days=2),
            start_time=time(10, 0),
            end_time=time(12, 0),
        )
        self.past_shift = self.past_event.shifts.first()


class PastShiftStudentTests(PastShiftGatingBase):
    def test_student_cannot_sign_up_for_past_shift(self):
        self.client.login(username="student", password="password")  # nosec B106
        url = reverse(
            "outreach:shift_signup", args=[self.program.id, self.past_shift.pk]
        )
        resp = self.client.post(url, {"role": OutreachSignup.HELPER}, follow=True)
        self.assertFalse(OutreachSignup.objects.filter(shift=self.past_shift).exists())
        messages = [str(m) for m in resp.context["messages"]]
        self.assertTrue(any("ended" in m.lower() for m in messages))

    def test_student_cannot_cancel_signup_for_past_shift(self):
        signup = OutreachSignup.objects.create(
            shift=self.past_shift, student=self.student, role=OutreachSignup.HELPER
        )
        self.client.login(username="student", password="password")  # nosec B106
        url = reverse(
            "outreach:shift_cancel", args=[self.program.id, self.past_shift.pk]
        )
        self.client.post(url)
        self.assertTrue(OutreachSignup.objects.filter(pk=signup.pk).exists())

    def test_template_hides_student_signup_forms_for_past_shift(self):
        self.client.login(username="student", password="password")  # nosec B106
        url = reverse("outreach:event_list", args=[self.program.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Sign up as Champion")
        self.assertNotContains(resp, "Sign up as Helper")

    def test_template_shows_ended_note_for_past_shift(self):
        self.client.login(username="student", password="password")  # nosec B106
        url = reverse("outreach:event_list", args=[self.program.id])
        resp = self.client.get(url)
        self.assertContains(resp, "This shift has ended.")


class PastShiftMentorTests(PastShiftGatingBase):
    def test_mentor_cannot_sign_up_to_support_past_shift(self):
        self.client.login(username="mentor", password="password")  # nosec B106
        url = reverse(
            "outreach:shift_mentor_signup", args=[self.program.id, self.past_shift.pk]
        )
        self.client.post(url)
        self.assertFalse(
            OutreachMentorSignup.objects.filter(shift=self.past_shift).exists()
        )

    def test_mentor_cannot_cancel_support_for_past_shift(self):
        signup = OutreachMentorSignup.objects.create(
            adult=self.mentor_adult, shift=self.past_shift
        )
        self.client.login(username="mentor", password="password")  # nosec B106
        url = reverse(
            "outreach:shift_mentor_cancel", args=[self.program.id, self.past_shift.pk]
        )
        self.client.post(url)
        self.assertTrue(OutreachMentorSignup.objects.filter(pk=signup.pk).exists())

    def test_mentor_can_manage_signups_for_past_shift(self):
        self.client.login(username="mentor", password="password")  # nosec B106
        url = reverse(
            "outreach:shift_manage_signups", args=[self.program.id, self.past_shift.pk]
        )
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

        post_resp = self.client.post(
            url, {"champions": [self.student.pk], "helpers": []}
        )
        self.assertIn(post_resp.status_code, (200, 302))
        self.assertTrue(
            OutreachSignup.objects.filter(
                shift=self.past_shift,
                student=self.student,
                role=OutreachSignup.CHAMPION,
            ).exists()
        )

    def test_mentor_sees_manage_button_for_past_shift(self):
        self.client.login(username="mentor", password="password")  # nosec B106
        url = reverse("outreach:event_list", args=[self.program.id])
        resp = self.client.get(url)
        self.assertContains(resp, "manage-signups/")


class PastShiftChampionTests(PastShiftGatingBase):
    def setUp(self):
        super().setUp()
        self.champion_signup = OutreachSignup.objects.create(
            shift=self.past_shift, student=self.student, role=OutreachSignup.CHAMPION
        )

    def test_champion_cannot_manage_signups_for_past_shift(self):
        self.client.login(username="student", password="password")  # nosec B106
        url = reverse(
            "outreach:shift_manage_signups", args=[self.program.id, self.past_shift.pk]
        )
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 302)

    def test_template_hides_champion_manage_button_for_past_shift(self):
        self.client.login(username="student", password="password")  # nosec B106
        url = reverse("outreach:event_list", args=[self.program.id])
        resp = self.client.get(url)
        self.assertNotContains(resp, "manage-signups/")


class PastShiftModelValidationTests(PastShiftGatingBase):
    def test_signup_clean_rejects_past_shift(self):
        signup = OutreachSignup(
            student=self.student, shift=self.past_shift, role=OutreachSignup.HELPER
        )
        with self.assertRaises(ValidationError):
            signup.clean()

    def test_signup_clean_allows_upcoming_shift(self):
        today = timezone.now().date()
        upcoming_event = create_outreach_event(
            program=self.program,
            name="Upcoming Event",
            location_name="Loc",
            location_address="Addr",
            start_date=today + timedelta(days=5),
            start_time=time(10, 0),
            end_time=time(12, 0),
        )
        signup = OutreachSignup(
            student=self.student,
            shift=upcoming_event.shifts.first(),
            role=OutreachSignup.HELPER,
        )
        signup.clean()
