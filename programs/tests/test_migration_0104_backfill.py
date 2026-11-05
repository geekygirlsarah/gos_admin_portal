from django.core.management import call_command
from django.db import connection
from django.db.migrations.recorder import MigrationRecorder
from django.test import TransactionTestCase

from organizations.models import Organization
from programs.models import School, SchoolDistrict


class SchoolDistrictBackfillMigrationTest(TransactionTestCase):
    """Regression tests for programs.0104_school_district.

    The original migration used AlterField to convert the legacy free-text
    ``School.district`` column into a ForeignKey before backfilling data.
    On PostgreSQL that generates ``USING "district_id"::bigint`` and fails
    with a DataError whenever any school still holds a district name.
    """

    def _insert_legacy_school(self, name, district):
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO programs_school (name, district) VALUES (%s, %s)",
                [name, district],
            )

    def test_backfill_after_flush(self):
        call_command("flush", interactive=False, verbosity=0)
        self.assertFalse(Organization.objects.filter(slug="gos").exists())
        self.assertIn(
            ("organizations", "0002_seed_gos_organization"),
            MigrationRecorder(connection).applied_migrations(),
        )
        self.test_backfill_creates_districts_and_links_schools()

    def test_backfill_creates_districts_and_links_schools(self):
        call_command("migrate", "programs", "0103", verbosity=0)

        self._insert_legacy_school(
            "Allegheny Valley HS", "Allegheny Valley School District"
        )
        self._insert_legacy_school(
            "Acmetonia Primary", "Allegheny Valley School District"
        )
        self._insert_legacy_school(
            "NA Senior High", "  North Allegheny School District "
        )
        self._insert_legacy_school("Blank District School", "")
        self._insert_legacy_school("Null District School", None)

        call_command("migrate", verbosity=0)

        av = SchoolDistrict.objects.get(name="Allegheny Valley School District")
        self.assertEqual(School.objects.get(name="Allegheny Valley HS").district, av)
        self.assertEqual(School.objects.get(name="Acmetonia Primary").district, av)
        na = SchoolDistrict.objects.get(name="North Allegheny School District")
        self.assertEqual(School.objects.get(name="NA Senior High").district, na)
        self.assertIsNone(School.objects.get(name="Blank District School").district)
        self.assertIsNone(School.objects.get(name="Null District School").district)
