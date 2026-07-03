import datetime

from django.test import TestCase
from django.utils import timezone

from applications.services import get_program_buckets
from programs.models import Program


class ProgramApplicationDatesTest(TestCase):
    def test_new_behavior_includes_started_programs_by_default(self):
        """
        Verify that programs that have already started are now included
        in the 'future' bucket by default (because applications_close
        defaults to end_date).
        """
        today = timezone.localdate()
        started_program = Program.objects.create(
            name="Started Yesterday",
            start_date=today - datetime.timedelta(days=1),
            end_date=today + datetime.timedelta(days=30),
            active=True,
        )

        future, current, past = get_program_buckets()

        # Now it IS in future by default!
        self.assertIn(started_program, future)
        self.assertNotIn(started_program, current)

    def test_new_behavior_includes_started_program_if_application_dates_allow(self):
        """
        Verify that a program that has started but has application dates set
        to include today IS included in the 'future' bucket.
        """
        today = timezone.localdate()
        started_program = Program.objects.create(
            name="Started Yesterday but Apps Open",
            start_date=today - datetime.timedelta(days=1),
            end_date=today + datetime.timedelta(days=30),
            applications_open=today - datetime.timedelta(days=5),
            applications_close=today + datetime.timedelta(days=5),
            active=True,
        )

        future, current, past = get_program_buckets()

        # This is what we want!
        self.assertIn(started_program, future)
        # It should probably still be in 'current' too if it's currently running?
        # Or should 'current' exclude programs that are in 'future'?
        # The docstring says:
        # - future: applications open.
        # - current: started already and not ended — applications closed.
        # So if applications are open, it should move to 'future'?
        # Or maybe it can be in both?
        # Usually 'buckets' implies mutually exclusive, but we'll see.
        self.assertNotIn(started_program, current)

    def test_active_box_overwrites_application_dates(self):
        """
        If 'active' is False, the program should not appear in 'future'
        even if the dates are within range.
        """
        today = timezone.localdate()
        inactive_program = Program.objects.create(
            name="Inactive but Dates OK",
            start_date=today + datetime.timedelta(days=10),
            applications_open=today - datetime.timedelta(days=5),
            applications_close=today + datetime.timedelta(days=5),
            active=False,
        )

        future, current, past = get_program_buckets()
        self.assertNotIn(inactive_program, future)
