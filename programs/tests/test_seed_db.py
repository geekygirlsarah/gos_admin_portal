from datetime import date

from django.test import TestCase


class SeedOrdersFeatureTests(TestCase):
    """The dev seed must register the ``orders`` ProgramFeature and enable it
    on the current flagship program so seeded students get the "Order
    Requests" navbar link (the navbar gates the link on the feature)."""

    def test_seed_registers_orders_feature(self):
        from programs.management.commands.seed_db import Command

        cmd = Command()
        features = cmd._seed_features()
        self.assertIn("orders", features)
        self.assertEqual(features["orders"].name, "Order Requests")

    def test_current_flagship_program_enables_orders(self):
        from programs.management.commands.seed_db import Command

        cmd = Command()
        this_year = date.today().year
        features = cmd._seed_features()
        programs = cmd._seed_programs(this_year, features)
        flagship = next(
            p for p in programs if p.name == f"Girls of Steel FRC {this_year}"
        )
        self.assertTrue(flagship.has_feature("orders"))
