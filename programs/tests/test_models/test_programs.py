import datetime

from django.test import TestCase

from programs.models import Program, ProgramFeature


class ProgramModelTests(TestCase):
    def setUp(self):
        self.program = Program.objects.create(name="Robotics 2025")

    def test_program_year_display(self):
        # Case 1: No dates
        p = Program(name="Test")
        self.assertEqual(p.year_display, "")
        self.assertEqual(str(p), "Test")

        # Case 2: Same year
        p.start_date = datetime.date(2025, 1, 1)
        p.end_date = datetime.date(2025, 12, 31)
        self.assertEqual(p.year_display, "2025")
        self.assertEqual(str(p), "Test (2025)")

        # Case 3: Different years
        p.end_date = datetime.date(2026, 1, 1)
        self.assertEqual(p.year_display, "2025-2026")
        self.assertEqual(str(p), "Test (2025-2026)")

        # Case 4: Only start date
        p.end_date = None
        self.assertEqual(p.year_display, "2025")

        # Case 5: Only end date
        p.start_date = None
        p.end_date = datetime.date(2026, 1, 1)
        self.assertEqual(p.year_display, "2026")

    def test_program_has_feature(self):
        feat, _ = ProgramFeature.objects.get_or_create(
            key="discord", defaults={"name": "Discord"}
        )
        self.assertFalse(self.program.has_feature("discord"))
        self.program.features.add(feat)
        self.assertTrue(self.program.has_feature("discord"))
        self.assertFalse(self.program.has_feature("background-checks"))

    def test_program_grade_range_fields(self):
        program = Program.objects.create(
            name="Test Program", grade_range_start=4, grade_range_end=6
        )
        self.assertEqual(program.grade_range_start, 4)
        self.assertEqual(program.grade_range_end, 6)

    def test_grade_range_display(self):
        p1 = Program.objects.create(name="P1", grade_range_start=4, grade_range_end=6)
        self.assertEqual(p1.grade_range_display, "4th–6th Grade")

        p2 = Program.objects.create(name="P2", grade_range_start=9, grade_range_end=12)
        self.assertEqual(p2.grade_range_display, "9th–12th Grade")

        p3 = Program.objects.create(name="P3", grade_range_start=0, grade_range_end=2)
        self.assertEqual(p3.grade_range_display, "K–2nd Grade")

        p4 = Program.objects.create(name="P4", grade_range_start=1, grade_range_end=1)
        self.assertEqual(p4.grade_range_display, "1st Grade")

        p5 = Program.objects.create(name="P5")
        self.assertEqual(p5.grade_range_display, "")
