"""Champion/mentor-operated check-in/out tracks actual attended hours.

The core rule that guards against "sneaking hours": a student is only
credited for the actual time between check-in and check-out on a past
shift, never the full scheduled shift. Champions can operate the check-in
page until everyone they're tracking has checked in and out (or the 4-hour
grace window after the shift ends); mentors can always revise.
"""

from datetime import datetime, time, timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from outreach.models import OutreachShift, OutreachSignup
from outreach.tests.factories import create_outreach_event, record_full_attendance
from outreach.utils import get_student_outreach_stats
from programs.models import (
    Adult,
    Enrollment,
    Program,
    ProgramFeature,
    School,
    Student,
)

CHECKIN_GRACE_HOURS = 4


def _shift_on_datetime(event, end_dt, hours=2):
    """Create a shift ending at ``end_dt`` (same-day start ``hours`` earlier)."""
    start_dt = end_dt - timedelta(hours=hours)
    return OutreachShift.objects.create(
        event=event,
        date=end_dt.date(),
        start_time=start_dt.time(),
        end_time=end_dt.time(),
    )


class CheckinBase(TestCase):
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

        # Champion of the grace-period/upcoming/old shifts.
        self.champion_user = User.objects.create_user(
            username="champion", password="password"
        )  # nosec B106
        self.champion = Student.objects.create(
            user=self.champion_user,
            legal_first_name="Champ",
            last_name="Lead",
            school=self.school,
            graduation_year=2027,
        )

        # Helper signed up to the grace-period/upcoming shifts.
        self.helper_user = User.objects.create_user(
            username="helper", password="password"
        )  # nosec B106
        self.helper = Student.objects.create(
            user=self.helper_user,
            legal_first_name="Helper",
            last_name="One",
            school=self.school,
            graduation_year=2027,
        )

        # A student who is not signed up (candidate for a walk-up).
        self.stranger_user = User.objects.create_user(
            username="stranger", password="password"
        )  # nosec B106
        self.stranger = Student.objects.create(
            user=self.stranger_user,
            legal_first_name="Strange",
            last_name="Two",
            school=self.school,
            graduation_year=2027,
        )
        # Walk-ups are picked from active program students.
        for student in (self.champion, self.helper, self.stranger):
            Enrollment.objects.create(
                student=student, program=self.program, active=True
            )

        # Upcoming shift (still in the future) — championed by the champion.
        later = timezone.now() + timedelta(hours=3)
        self.upcoming_event = create_outreach_event(
            program=self.program,
            name="Upcoming Event",
            location_name="Loc",
            location_address="Addr",
            start_date=later.date(),
            start_time=later.time(),
            end_time=(later + timedelta(hours=2)).time(),
        )
        self.upcoming_shift = self.upcoming_event.shifts.first()
        OutreachSignup.objects.create(
            shift=self.upcoming_shift,
            student=self.champion,
            role=OutreachSignup.CHAMPION,
        )
        self.helper_upcoming_signup = OutreachSignup.objects.create(
            shift=self.upcoming_shift, student=self.helper, role=OutreachSignup.HELPER
        )

        # Shift that ended just now ("within grace") — champion may still edit.
        self.grace_event = create_outreach_event(
            program=self.program,
            name="Grace Event",
            location_name="Loc",
            location_address="Addr",
            start_date=timezone.now().date(),
            start_time=time(0, 0),
            end_time=time(0, 30),
        )
        self.grace_shift = _shift_on_datetime(
            self.grace_event, timezone.now() - timedelta(hours=2)
        )
        self.champion_signup = OutreachSignup.objects.create(
            shift=self.grace_shift,
            student=self.champion,
            role=OutreachSignup.CHAMPION,
        )
        self.helper_signup = OutreachSignup.objects.create(
            shift=self.grace_shift, student=self.helper, role=OutreachSignup.HELPER
        )

        # Shift that ended too long ago — champion is read-only, mentor is not.
        old_day = timezone.now() - timedelta(days=2)
        self.old_event = create_outreach_event(
            program=self.program,
            name="Old Event",
            location_name="Loc",
            location_address="Addr",
            start_date=old_day.date(),
            start_time=old_day.time(),
            end_time=(old_day + timedelta(hours=1)).time(),
        )
        self.old_shift = self.old_event.shifts.first()
        self.old_signup = OutreachSignup.objects.create(
            shift=self.old_shift, student=self.helper, role=OutreachSignup.HELPER
        )
        self.old_champion_signup = OutreachSignup.objects.create(
            shift=self.old_shift, student=self.champion, role=OutreachSignup.CHAMPION
        )

    def _url(self, shift):
        return reverse("outreach:shift_check_in", args=[self.program.id, shift.pk])


class CheckinAccessTests(CheckinBase):
    def test_page_accessible_to_mentor(self):
        self.client.login(username="mentor", password="password")  # nosec B106
        resp = self.client.get(self._url(self.grace_shift))
        self.assertEqual(resp.status_code, 200)

    def test_page_accessible_to_shift_champion(self):
        self.client.login(username="champion", password="password")  # nosec B106
        resp = self.client.get(self._url(self.grace_shift))
        self.assertEqual(resp.status_code, 200)

    def test_page_denied_to_non_champion_student(self):
        self.client.login(username="helper", password="password")  # nosec B106
        resp = self.client.get(self._url(self.grace_shift))
        self.assertEqual(resp.status_code, 302)

    def test_page_denied_to_unrelated_student(self):
        self.client.login(username="stranger", password="password")  # nosec B106
        resp = self.client.get(self._url(self.grace_shift))
        self.assertEqual(resp.status_code, 302)

    def test_champion_can_view_read_only_after_grace(self):
        self.client.login(username="champion", password="password")  # nosec B106
        resp = self.client.get(self._url(self.old_shift))
        self.assertEqual(resp.status_code, 200)


class CheckinActionsTests(CheckinBase):
    def test_mentor_can_check_students_in_and_out(self):
        self.client.login(username="mentor", password="password")  # nosec B106
        url = self._url(self.grace_shift)
        before = timezone.now()
        self.client.post(url, {"action": "check_in", "student_id": self.helper.pk})
        self.assertIsNotNone(
            OutreachSignup.objects.get(pk=self.helper_signup.pk).checked_in_at
        )
        self.client.post(url, {"action": "check_out", "student_id": self.helper.pk})
        after = timezone.now()
        signup = OutreachSignup.objects.get(pk=self.helper_signup.pk)
        self.assertGreaterEqual(signup.checked_in_at, before - timedelta(seconds=5))
        self.assertLessEqual(signup.checked_out_at, after + timedelta(seconds=5))

    def test_champion_can_check_students_in_and_out(self):
        self.client.login(username="champion", password="password")  # nosec B106
        url = self._url(self.grace_shift)
        self.client.post(url, {"action": "check_in", "student_id": self.helper.pk})
        self.client.post(url, {"action": "check_out", "student_id": self.helper.pk})
        signup = OutreachSignup.objects.get(pk=self.helper_signup.pk)
        self.assertIsNotNone(signup.checked_in_at)
        self.assertIsNotNone(signup.checked_out_at)

    def test_champion_can_check_early_setup_before_shift(self):
        self.client.login(username="champion", password="password")  # nosec B106
        url = self._url(self.upcoming_shift)
        self.client.post(url, {"action": "check_in", "student_id": self.helper.pk})
        signup = OutreachSignup.objects.get(pk=self.helper_upcoming_signup.pk)
        self.assertIsNotNone(signup.checked_in_at)

    def test_bulk_check_in_and_out(self):
        self.client.login(username="mentor", password="password")  # nosec B106
        url = self._url(self.grace_shift)
        self.client.post(url, {"action": "check_in_all"})
        self.client.post(url, {"action": "check_out_all"})
        for signup in OutreachSignup.objects.filter(shift=self.grace_shift):
            self.assertIsNotNone(signup.checked_in_at)
            self.assertIsNotNone(signup.checked_out_at)

    def test_check_in_to_unknown_student_is_ignored(self):
        self.client.login(username="mentor", password="password")  # nosec B106
        url = self._url(self.grace_shift)
        self.client.post(url, {"action": "check_in", "student_id": self.stranger.pk})
        self.assertIsNone(
            OutreachSignup.objects.get(pk=self.helper_signup.pk).checked_in_at
        )

    def test_walk_up_creates_signup_and_checks_in(self):
        self.client.login(username="mentor", password="password")  # nosec B106
        url = self._url(self.grace_shift)
        self.client.post(url, {"action": "walk_up", "student_id": self.stranger.pk})
        signup = OutreachSignup.objects.get(
            shift=self.grace_shift, student=self.stranger
        )
        self.assertEqual(signup.role, OutreachSignup.HELPER)
        self.assertIsNotNone(signup.checked_in_at)

    def test_walk_up_for_signed_up_student_is_idempotent(self):
        self.client.login(username="mentor", password="password")  # nosec B106
        url = self._url(self.grace_shift)
        self.client.post(url, {"action": "walk_up", "student_id": self.helper.pk})
        self.assertEqual(
            OutreachSignup.objects.filter(
                shift=self.grace_shift, student=self.helper
            ).count(),
            1,
        )
        self.assertIsNotNone(
            OutreachSignup.objects.get(pk=self.helper_signup.pk).checked_in_at
        )

    def test_check_in_to_wrong_program_student_is_ignored(self):
        """A student from another program can't be added as a walk-up."""
        other_prog = Program.objects.create(name="Other Program")
        other_feature, _ = ProgramFeature.objects.get_or_create(
            key="outreach", defaults={"name": "Outreach"}
        )
        other_prog.features.add(other_feature)
        other_user = User.objects.create_user(
            username="outside", password="password"
        )  # nosec B106
        outside = Student.objects.create(
            user=other_user,
            legal_first_name="Out",
            last_name="Side",
            school=self.school,
            graduation_year=2027,
        )
        self.client.login(username="mentor", password="password")  # nosec B106
        url = self._url(self.grace_shift)
        self.client.post(url, {"action": "walk_up", "student_id": outside.pk})
        self.assertFalse(
            OutreachSignup.objects.filter(
                shift=self.grace_shift, student=outside
            ).exists()
        )

    def test_champion_locked_after_all_checked_out(self):
        """Once every student has checked in and out, the champion is read-only."""
        now = timezone.now()
        for signup in OutreachSignup.objects.filter(shift=self.grace_shift):
            signup.checked_in_at = now - timedelta(hours=2)
            signup.checked_out_at = now - timedelta(minutes=30)
            signup.save(update_fields=["checked_in_at", "checked_out_at"])

        self.client.login(username="champion", password="password")  # nosec B106
        url = self._url(self.grace_shift)
        resp = self.client.post(
            url, {"action": "check_out", "student_id": self.helper.pk}
        )
        self.assertEqual(resp.status_code, 302)
        before = OutreachSignup.objects.get(pk=self.helper_signup.pk).checked_out_at
        self.assertIsNotNone(before)

    def test_champion_locked_after_grace_period(self):
        self.client.login(username="champion", password="password")  # nosec B106
        url = self._url(self.old_shift)
        resp = self.client.post(
            url, {"action": "check_in", "student_id": self.helper.pk}
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIsNone(
            OutreachSignup.objects.get(pk=self.old_signup.pk).checked_in_at
        )

    def test_mentor_can_edit_after_grace_period(self):
        self.client.login(username="mentor", password="password")  # nosec B106
        url = self._url(self.old_shift)
        self.client.post(url, {"action": "check_out", "student_id": self.helper.pk})
        self.assertIsNotNone(
            OutreachSignup.objects.get(pk=self.old_signup.pk).checked_out_at
        )


class CheckinSetTimesTests(CheckinBase):
    """Operators can correct/backdate check-in/out times after the fact.

    Students occasionally forget to check in, or the volunteer recording it
    taps the wrong time. ``set_times`` lets whoever can operate the check-in
    page (mentors always; a champion until their access lapses) correct a
    signup's timestamps without needing Django admin access.
    """

    def _post(self, url, **kwargs):
        return self.client.post(url, {"action": "set_times", **kwargs})

    def test_mentor_can_set_both_times_for_forgotten_checkin(self):
        self.client.login(username="mentor", password="password")  # nosec B106
        url = self._url(self.old_shift)
        check_in = "2026-08-01T09:00"
        check_out = "2026-08-01T11:00"
        self._post(
            url,
            student_id=self.helper.pk,
            checked_in_at=check_in,
            checked_out_at=check_out,
        )
        signup = OutreachSignup.objects.get(pk=self.old_signup.pk)
        self.assertIsNotNone(signup.checked_in_at)
        self.assertIsNotNone(signup.checked_out_at)
        self.assertEqual(
            signup.checked_in_at.astimezone(timezone.get_current_timezone()).strftime(
                "%Y-%m-%dT%H:%M"
            ),
            check_in,
        )
        self.assertEqual(
            signup.checked_out_at.astimezone(timezone.get_current_timezone()).strftime(
                "%Y-%m-%dT%H:%M"
            ),
            check_out,
        )

    def test_mentor_can_fill_in_only_a_missing_check_in(self):
        self.client.login(username="mentor", password="password")  # nosec B106
        url = self._url(self.grace_shift)
        signup = OutreachSignup.objects.get(pk=self.helper_signup.pk)
        existing_out = timezone.now() - timedelta(hours=1)
        signup.checked_out_at = existing_out
        signup.save(update_fields=["checked_out_at"])
        # The edit form is pre-filled with the current check-out, so it's
        # submitted along with the new check-in; the check-in was blank.
        local_out = existing_out.astimezone(timezone.get_current_timezone())
        self._post(
            url,
            student_id=self.helper.pk,
            checked_in_at="2026-08-01T09:00",
            checked_out_at=local_out.strftime("%Y-%m-%dT%H:%M"),
        )
        signup.refresh_from_db()
        self.assertIsNotNone(signup.checked_in_at)
        self.assertAlmostEqual(
            signup.checked_out_at, existing_out, delta=timedelta(minutes=2)
        )

    def test_clearing_both_times_unsets_them(self):
        self.client.login(username="mentor", password="password")  # nosec B106
        url = self._url(self.old_shift)
        signup = OutreachSignup.objects.get(pk=self.old_signup.pk)
        signup.checked_in_at = timezone.now() - timedelta(hours=2)
        signup.checked_out_at = timezone.now() - timedelta(hours=1)
        signup.save(update_fields=["checked_in_at", "checked_out_at"])
        self._post(url, student_id=self.helper.pk)
        signup.refresh_from_db()
        self.assertIsNone(signup.checked_in_at)
        self.assertIsNone(signup.checked_out_at)

    def test_champion_can_set_times_while_within_access(self):
        self.client.login(username="champion", password="password")  # nosec B106
        url = self._url(self.grace_shift)
        check_in = "2026-08-01T09:30"
        self._post(url, student_id=self.helper.pk, checked_in_at=check_in)
        signup = OutreachSignup.objects.get(pk=self.helper_signup.pk)
        self.assertIsNotNone(signup.checked_in_at)
        self.assertEqual(
            signup.checked_in_at.astimezone(timezone.get_current_timezone()).strftime(
                "%Y-%m-%dT%H:%M"
            ),
            check_in,
        )

    def test_champion_cannot_set_times_after_access_lapses(self):
        self.client.login(username="champion", password="password")  # nosec B106
        url = self._url(self.old_shift)
        self._post(url, student_id=self.helper.pk, checked_in_at="2026-08-01T09:00")
        self.assertIsNone(
            OutreachSignup.objects.get(pk=self.old_signup.pk).checked_in_at
        )

    def test_set_times_rejects_out_before_in(self):
        self.client.login(username="mentor", password="password")  # nosec B106
        url = self._url(self.grace_shift)
        self._post(
            url,
            student_id=self.helper.pk,
            checked_in_at="2026-08-01T11:00",
            checked_out_at="2026-08-01T09:00",
        )
        signup = OutreachSignup.objects.get(pk=self.helper_signup.pk)
        self.assertIsNone(signup.checked_in_at)
        self.assertIsNone(signup.checked_out_at)

    def test_set_times_ignores_unknown_student(self):
        self.client.login(username="mentor", password="password")  # nosec B106
        url = self._url(self.old_shift)
        self._post(url, student_id=self.stranger.pk, checked_in_at="2026-08-01T09:00")
        self.assertIsNone(
            OutreachSignup.objects.get(pk=self.old_signup.pk).checked_in_at
        )

    def test_set_times_invalid_datetime_is_ignored(self):
        self.client.login(username="mentor", password="password")  # nosec B106
        url = self._url(self.grace_shift)
        self._post(url, student_id=self.helper.pk, checked_in_at="not-a-datetime")
        signup = OutreachSignup.objects.get(pk=self.helper_signup.pk)
        self.assertIsNone(signup.checked_in_at)
        self.assertIsNone(signup.checked_out_at)


class CheckinSetTimesUITests(CheckinBase):
    def test_edit_times_form_shown_to_operator(self):
        self.client.login(username="mentor", password="password")  # nosec B106
        resp = self.client.get(self._url(self.grace_shift))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "set_times")
        self.assertContains(resp, 'name="checked_in_at"')
        self.assertContains(resp, 'name="checked_out_at"')
        self.assertContains(resp, "Save times")

    def test_edit_times_form_hidden_when_locked(self):
        self.client.login(username="champion", password="password")  # nosec B106
        resp = self.client.get(self._url(self.old_shift))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "set_times")
        self.assertNotContains(resp, 'name="checked_in_at"')


class AttendedHoursTests(CheckinBase):
    """Uses ``self.stranger`` (no other signups) for clean per-shift math."""

    def _ended_shift_with_signup(self, days_ago=1, hours=2):
        day = timezone.now().date() - timedelta(days=days_ago)
        end_dt = timezone.make_aware(datetime.combine(day, time(12, 0)))
        start_dt = end_dt - timedelta(hours=hours)
        event = create_outreach_event(
            program=self.program,
            name=f"Attend {day.isoformat()}",
            location_name="Loc",
            location_address="Addr",
            start_date=end_dt.date(),
            start_time=start_dt.time(),
            end_time=end_dt.time(),
        )
        shift = event.shifts.first()
        signup = OutreachSignup.objects.create(
            shift=shift, student=self.stranger, role=OutreachSignup.HELPER
        )
        return shift, signup

    def test_late_arrival_credits_actual_hours_only(self):
        """A student who shows up late gets credit only for attended time,
        not the full scheduled shift."""
        shift, signup = self._ended_shift_with_signup(hours=2)
        start_dt = timezone.make_aware(datetime.combine(shift.date, shift.start_time))
        end_dt = timezone.make_aware(datetime.combine(shift.date, shift.end_time))
        signup.checked_in_at = start_dt + timedelta(hours=1)
        signup.checked_out_at = end_dt
        signup.save(update_fields=["checked_in_at", "checked_out_at"])

        stats = get_student_outreach_stats(self.stranger, self.program)
        self.assertAlmostEqual(stats["total_outreach_hours"], 1.0, places=2)
        self.assertNotEqual(stats["total_outreach_hours"], shift.duration_hours)

    def test_early_setup_and_late_cleanup_credited(self):
        """Students who arrive early for setup / stay late for cleanup get
        credited for that extra (actual) time."""
        shift, signup = self._ended_shift_with_signup(hours=2)
        start_dt = timezone.make_aware(datetime.combine(shift.date, shift.start_time))
        end_dt = timezone.make_aware(datetime.combine(shift.date, shift.end_time))
        signup.checked_in_at = start_dt - timedelta(minutes=30)
        signup.checked_out_at = end_dt + timedelta(minutes=45)
        signup.save(update_fields=["checked_in_at", "checked_out_at"])

        stats = get_student_outreach_stats(self.stranger, self.program)
        self.assertAlmostEqual(
            stats["total_outreach_hours"], 2.0 + 0.5 + 0.75, places=2
        )

    def test_past_signup_without_times_is_pending(self):
        """A past shift nobody stamped is 'pending' (awaiting confirmation)."""
        shift, _ = self._ended_shift_with_signup(hours=2)
        stats = get_student_outreach_stats(self.stranger, self.program)
        self.assertEqual(stats["total_outreach_hours"], 0.0)
        self.assertAlmostEqual(
            stats["pending_outreach_hours"], shift.duration_hours, places=2
        )
        self.assertEqual(stats["unconfirmed_count"], 1)

    def test_finalized_past_signup_is_completed(self):
        shift, signup = self._ended_shift_with_signup(hours=2)
        record_full_attendance(signup, shift)
        stats = get_student_outreach_stats(self.stranger, self.program)
        self.assertAlmostEqual(
            stats["total_outreach_hours"], shift.duration_hours, places=2
        )
        self.assertEqual(stats["unconfirmed_count"], 0)
