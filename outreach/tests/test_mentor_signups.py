from datetime import date, time, timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from outreach.models import OutreachMentorSignup
from outreach.tests.factories import create_outreach_event
from programs.models import (
    Adult,
    Enrollment,
    Program,
    ProgramFeature,
    School,
    Student,
)


def _next_year_month_day():
    """A date safely in the future relative to the test run."""
    return date.today() + timedelta(days=180)


class MentorSignupModelTest(TestCase):
    def setUp(self):
        self.event = create_outreach_event(
            name="Test Event",
            location_name="Test Location",
            location_address="123 Test St",
            start_date=_next_year_month_day(),
            start_time=time(10, 0),
            end_time=time(12, 0),
        )
        self.shift = self.event.shifts.first()

    def test_create_mentor_signup(self):
        adult = Adult.objects.create(legal_first_name="Mentor", last_name="One")
        signup = OutreachMentorSignup.objects.create(adult=adult, shift=self.shift)
        self.assertEqual(signup.event, self.event)
        self.assertIn(signup, self.shift.mentor_signups.all())
        self.assertEqual(str(signup), "Mentor One - Test Event")

    def test_adult_cannot_sign_up_twice_for_same_shift(self):
        adult = Adult.objects.create(legal_first_name="Mentor", last_name="One")
        OutreachMentorSignup.objects.create(adult=adult, shift=self.shift)
        with self.assertRaises(Exception):
            OutreachMentorSignup.objects.create(adult=adult, shift=self.shift)

    def test_no_limit_on_mentors_per_shift(self):
        # Unlike student champion/helper roles there is no capacity cap.
        for i in range(10):
            adult = Adult.objects.create(legal_first_name="M", last_name=str(i))
            OutreachMentorSignup.objects.create(adult=adult, shift=self.shift)
        self.assertEqual(self.shift.mentor_signups.count(), 10)

    def test_event_mentors_spans_all_shifts(self):
        second_shift = self.event.shifts.create(
            date=_next_year_month_day(),
            start_time=time(13, 0),
            end_time=time(15, 0),
        )
        adult1 = Adult.objects.create(legal_first_name="Mentor", last_name="One")
        adult2 = Adult.objects.create(legal_first_name="Mentor", last_name="Two")
        OutreachMentorSignup.objects.create(adult=adult1, shift=self.shift)
        OutreachMentorSignup.objects.create(adult=adult2, shift=second_shift)
        self.assertEqual(self.event.mentors.count(), 2)


class MentorSignupViewTest(TestCase):
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
            user=self.mentor_user,
            legal_first_name="Molly",
            last_name="Mentor",
            is_mentor=True,
            mentor_active=True,
        )

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
        Enrollment.objects.create(
            student=self.student_profile, program=self.program, active=True
        )

        self.parent_user = User.objects.create_user(
            username="parent", password="password"
        )  # nosec B106
        self.parent_adult = Adult.objects.create(
            user=self.parent_user,
            legal_first_name="Paula",
            last_name="Parent",
            is_parent=True,
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
        self.signup_url = reverse(
            "outreach:shift_mentor_signup", args=[self.program.id, self.shift.pk]
        )
        self.cancel_url = reverse(
            "outreach:shift_mentor_cancel", args=[self.program.id, self.shift.pk]
        )
        self.list_url = reverse("outreach:event_list", args=[self.program.id])

    def test_mentor_can_sign_up(self):
        self.client.login(username="mentor", password="password")  # nosec B106
        resp = self.client.post(self.signup_url)
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(
            OutreachMentorSignup.objects.filter(
                adult=self.mentor_adult, shift=self.shift
            ).exists()
        )

    def test_mentor_signup_is_idempotent(self):
        self.client.login(username="mentor", password="password")  # nosec B106
        self.client.post(self.signup_url)
        self.client.post(self.signup_url)
        self.assertEqual(
            OutreachMentorSignup.objects.filter(
                adult=self.mentor_adult, shift=self.shift
            ).count(),
            1,
        )

    def test_student_cannot_sign_up_as_mentor(self):
        self.client.login(username="student", password="password")  # nosec B106
        resp = self.client.post(self.signup_url)
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(OutreachMentorSignup.objects.filter(shift=self.shift).exists())

    def test_parent_without_mentor_flag_cannot_sign_up(self):
        self.client.login(username="parent", password="password")  # nosec B106
        resp = self.client.post(self.signup_url)
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(OutreachMentorSignup.objects.filter(shift=self.shift).exists())

    def test_parent_who_is_also_mentor_can_sign_up(self):
        self.parent_adult.is_mentor = True
        self.parent_adult.save()
        self.client.login(username="parent", password="password")  # nosec B106
        resp = self.client.post(self.signup_url)
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(
            OutreachMentorSignup.objects.filter(
                adult=self.parent_adult, shift=self.shift
            ).exists()
        )

    def test_anonymous_redirected_to_login(self):
        resp = self.client.post(self.signup_url)
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(OutreachMentorSignup.objects.filter(shift=self.shift).exists())

    def test_mentor_can_cancel_signup(self):
        OutreachMentorSignup.objects.create(adult=self.mentor_adult, shift=self.shift)
        self.client.login(username="mentor", password="password")  # nosec B106
        resp = self.client.post(self.cancel_url)
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(
            OutreachMentorSignup.objects.filter(
                adult=self.mentor_adult, shift=self.shift
            ).exists()
        )

    def test_cancel_without_signup_shows_error(self):
        self.client.login(username="mentor", password="password")  # nosec B106
        resp = self.client.post(self.cancel_url, follow=True)
        messages = [str(m) for m in resp.context["messages"]]
        self.assertTrue(any("not signed up" in m.lower() for m in messages))

    def test_other_mentor_cannot_cancel_someone_elses_signup(self):
        other_user = User.objects.create_user(
            username="other", password="password"
        )  # nosec B106
        other_adult = Adult.objects.create(
            user=other_user,
            legal_first_name="Olive",
            last_name="Other",
            is_mentor=True,
            mentor_active=True,
        )
        signup = OutreachMentorSignup.objects.create(
            adult=other_adult, shift=self.shift
        )
        self.client.login(username="mentor", password="password")  # nosec B106
        self.client.post(self.cancel_url)
        self.assertTrue(OutreachMentorSignup.objects.filter(pk=signup.pk).exists())


class MentorSignupVisibilityTest(TestCase):
    """Mentor names on shifts are visible to everyone who can see events."""

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
            user=self.mentor_user,
            legal_first_name="Molly",
            last_name="Mentor",
            is_mentor=True,
            mentor_active=True,
        )

        self.student_user = User.objects.create_user(
            username="student", password="password"
        )  # nosec B106
        Student.objects.create(
            user=self.student_user,
            legal_first_name="Test",
            last_name="Student",
            school=self.school,
            graduation_year=2027,
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
        OutreachMentorSignup.objects.create(adult=self.mentor_adult, shift=self.shift)
        self.list_url = reverse("outreach:event_list", args=[self.program.id])
        self.signup_url = reverse(
            "outreach:shift_mentor_signup", args=[self.program.id, self.shift.pk]
        )
        self.cancel_url = reverse(
            "outreach:shift_mentor_cancel", args=[self.program.id, self.shift.pk]
        )

    def test_names_visible_to_students(self):
        self.client.login(username="student", password="password")  # nosec B106
        resp = self.client.get(self.list_url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Molly Mentor")

    def test_signed_up_mentor_sees_cancel_button(self):
        self.client.login(username="mentor", password="password")  # nosec B106
        resp = self.client.get(self.list_url)
        self.assertContains(resp, self.cancel_url)

    def test_unsigned_mentor_sees_support_button(self):
        other_user = User.objects.create_user(
            username="other", password="password"
        )  # nosec B106
        Adult.objects.create(
            user=other_user,
            legal_first_name="Olive",
            last_name="Other",
            is_mentor=True,
            mentor_active=True,
        )
        self.client.login(username="other", password="password")  # nosec B106
        resp = self.client.get(self.list_url)
        self.assertContains(resp, self.signup_url)

    def test_student_does_not_see_support_button(self):
        self.client.login(username="student", password="password")  # nosec B106
        resp = self.client.get(self.list_url)
        self.assertNotContains(resp, self.signup_url)
