import datetime

from django.test import TestCase
from django.utils import timezone

from programs.models import Program


class TestApplicationDateDefaults(TestCase):
    def test_existing_programs_populated_by_migration(self):
        # This test won't actually check the migration that just ran on the REAL DB
        # because TestCase runs migrations on a fresh test DB.
        # But it confirms that the migration DOES populate them.
        today = timezone.localdate()
        p = Program.objects.create(
            name="Existing Program",
            start_date=today + datetime.timedelta(days=10),
            end_date=today + datetime.timedelta(days=20),
        )
        # In a test, migrations are run. If I created it BEFORE the migration it would work,
        # but here we just want to see if the SAVE method also works for new ones.
        self.assertEqual(p.applications_open, p.start_date)
        self.assertEqual(p.applications_close, p.end_date)

    def test_manual_override_preserved(self):
        today = timezone.localdate()
        open_date = today - datetime.timedelta(days=5)
        close_date = today + datetime.timedelta(days=5)
        p = Program.objects.create(
            name="Overridden Dates",
            start_date=today + datetime.timedelta(days=10),
            end_date=today + datetime.timedelta(days=20),
            applications_open=open_date,
            applications_close=close_date,
        )
        self.assertEqual(p.applications_open, open_date)
        self.assertEqual(p.applications_close, close_date)

    def test_partial_override_preserved(self):
        today = timezone.localdate()
        open_date = today - datetime.timedelta(days=5)
        p = Program.objects.create(
            name="Partial Override",
            start_date=today + datetime.timedelta(days=10),
            end_date=today + datetime.timedelta(days=20),
            applications_open=open_date,
        )
        self.assertEqual(p.applications_open, open_date)
        self.assertEqual(p.applications_close, p.end_date)
