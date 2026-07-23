from datetime import date

from django.test import TestCase

from programs.models import Fee, Program


class FeeEffectiveDateTest(TestCase):
    def test_fee_effective_date_field(self):
        program = Program.objects.create(name="Test Program")
        effective_date = date(2026, 7, 1)
        # We expect this to work after we fix the model
        fee = Fee.objects.create(
            program=program,
            name="Test Fee",
            amount=50.00,
            effective_date=effective_date,
        )
        self.assertEqual(fee.effective_date, effective_date)
