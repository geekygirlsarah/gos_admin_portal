import datetime
import importlib
from unittest.mock import MagicMock

from django.test import TestCase


class MigrateAdultClearancesTest(TestCase):
    """Verify migration 0094 maps legacy Adult clearance fields to
    BackgroundCheck rows correctly.

    Because the legacy fields no longer exist on the current models, we mock
    the historical app registry that the migration would receive.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.migration_module = importlib.import_module(
            "programs.migrations.0094_migrate_adult_clearances"
        )

    def _make_adult(self, paca, patch, fbi, expires):
        adult = MagicMock()
        adult.has_paca_clearance = paca
        adult.has_patch_clearance = patch
        adult.has_fbi_clearance = fbi
        adult.pa_clearances_expiration_date = expires
        return adult

    def _apps(self, adults, background_check):
        AdultModel = MagicMock()
        AdultModel.objects.all.return_value = adults

        def fake_apps(app_label, model):
            return {
                ("programs", "Adult"): AdultModel,
                ("programs", "BackgroundCheck"): background_check,
            }.get((app_label, model))

        apps = MagicMock()
        apps.get_model.side_effect = fake_apps
        return apps

    def test_creates_background_check_rows_for_cleared_fields(self):
        adult = self._make_adult(
            paca=True, patch=True, fbi=False, expires=datetime.date(2027, 12, 31)
        )
        BackgroundCheck = MagicMock()

        self.migration_module.migrate_adult_clearances(
            self._apps([adult], BackgroundCheck), None
        )

        created_kwargs = [
            c.kwargs for c in BackgroundCheck.objects.create.call_args_list
        ]
        # Two cleared fields → two rows, expired ones get the shared expiry date.
        self.assertEqual(len(created_kwargs), 2)
        self.assertEqual(
            {c["check_type"] for c in created_kwargs}, {"child_abuse", "state_police"}
        )
        for c in created_kwargs:
            self.assertEqual(c["adult"], adult)
            self.assertTrue(c["cleared"])
            self.assertEqual(c["obtained_date"], datetime.date(2022, 12, 31))

    def test_no_rows_when_no_clearances(self):
        adult = self._make_adult(paca=False, patch=False, fbi=False, expires=None)
        BackgroundCheck = MagicMock()

        self.migration_module.migrate_adult_clearances(
            self._apps([adult], BackgroundCheck), None
        )

        BackgroundCheck.objects.create.assert_not_called()
