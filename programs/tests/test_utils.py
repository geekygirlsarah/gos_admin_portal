import datetime
import string

from django.test import TestCase

from programs.models import Adult, Student
from programs.utils import (
    convert_student_to_alumni,
    find_matching_alumni_adult,
    generate_otp,
    row_raw,
    row_val,
    row_val_bool,
    row_val_date,
)


class UtilsTests(TestCase):
    def test_generate_otp_default_length(self):
        otp = generate_otp()
        self.assertEqual(len(otp), 6)
        self.assertTrue(all(c in string.digits for c in otp))

    def test_generate_otp_custom_length(self):
        otp = generate_otp(length=8)
        self.assertEqual(len(otp), 8)
        self.assertTrue(all(c in string.digits for c in otp))

    def test_generate_otp_is_random(self):
        # Very basic check that it's not returning the same thing every time
        otp1 = generate_otp()
        otp2 = generate_otp()
        self.assertNotEqual(otp1, otp2)


class RowHelpersTests(TestCase):
    def test_row_raw_returns_first_non_none(self):
        self.assertEqual(row_raw({"a": None, "b": 5}, "a", "b"), 5)
        self.assertIsNone(row_raw({"a": None}, "a", "missing"))

    def test_row_val_trims_and_skips_none_literal(self):
        self.assertEqual(row_val({"name": "  Alice  "}, "name"), "Alice")
        self.assertIsNone(row_val({"name": "None"}, "name"))
        self.assertIsNone(row_val({"name": ""}, "name"))
        self.assertEqual(row_val({"a": "", "b": "x"}, "a", "b"), "x")

    def test_row_val_bool_parses_common_spellings(self):
        for truthy in ("y", "Yes", "TRUE", "1", "t"):
            self.assertIs(row_val_bool({"v": truthy}, "v"), True)
        for falsy in ("n", "No", "false", "0", "f"):
            self.assertIs(row_val_bool({"v": falsy}, "v"), False)
        self.assertIsNone(row_val_bool({"v": "maybe"}, "v"))
        self.assertIsNone(row_val_bool({}, "v"))

    def test_row_val_date_handles_dates_and_strings(self):
        d = datetime.date(2024, 5, 1)
        self.assertEqual(row_val_date({"d": d}, "d"), d)
        self.assertEqual(
            row_val_date({"d": datetime.datetime(2024, 5, 1, 10, 0)}, "d"), d
        )
        self.assertEqual(row_val_date({"d": "2024-05-01"}, "d"), d)
        self.assertEqual(row_val_date({"d": "5/1/2024"}, "d"), d)
        self.assertIsNone(row_val_date({"d": "not a date"}, "d"))
        self.assertIsNone(row_val_date({}, "d"))


class AlumniConversionTests(TestCase):
    def _make_student(self, **kwargs):
        defaults = {
            "legal_first_name": "Sam",
            "first_name": "Sam",
            "last_name": "Jones",
        }
        defaults.update(kwargs)
        return Student.objects.create(**defaults)

    def test_find_matching_alumni_adult_by_alumni_email(self):
        adult = Adult.objects.create(
            first_name="X", last_name="Y", alumni_email="sam@example.com"
        )
        student = self._make_student(personal_email="SAM@example.com")
        self.assertEqual(find_matching_alumni_adult(student), adult)

    def test_find_matching_alumni_adult_by_name_and_flag(self):
        adult = Adult.objects.create(
            first_name="Sam", last_name="Jones", is_alumni=True
        )
        non_alumni = Adult.objects.create(
            first_name="Sam", last_name="Jones", is_alumni=False
        )
        student = self._make_student()
        match = find_matching_alumni_adult(student)
        self.assertEqual(match, adult)
        self.assertNotEqual(match, non_alumni)

    def test_find_matching_alumni_adult_returns_none(self):
        student = self._make_student()
        self.assertIsNone(find_matching_alumni_adult(student))

    def test_convert_student_to_alumni_creates_new_adult(self):
        student = self._make_student(personal_email="sam@example.com")
        adult, created, marked = convert_student_to_alumni(student)
        self.assertTrue(created)
        self.assertTrue(marked)
        self.assertTrue(adult.is_alumni)
        self.assertEqual(adult.alumni_email, "sam@example.com")
        student.refresh_from_db()
        self.assertTrue(student.graduated)

    def test_convert_student_to_alumni_updates_existing(self):
        # Match by alumni_email so the existing Adult is found and updated.
        existing = Adult.objects.create(
            first_name="Sam",
            last_name="Jones",
            alumni_email="sam@example.com",
            is_alumni=False,
        )
        student = self._make_student(personal_email="sam@example.com")
        adult, created, marked = convert_student_to_alumni(student)
        self.assertFalse(created)
        self.assertEqual(adult.pk, existing.pk)
        existing.refresh_from_db()
        self.assertTrue(existing.is_alumni)
        self.assertEqual(existing.alumni_email, "sam@example.com")
        self.assertTrue(marked)

    def test_convert_student_to_alumni_is_idempotent(self):
        student = self._make_student(personal_email="sam@example.com")
        convert_student_to_alumni(student)
        adult2, created2, marked2 = convert_student_to_alumni(student)
        self.assertFalse(created2)
        self.assertFalse(marked2)
        self.assertEqual(
            Adult.objects.filter(alumni_email="sam@example.com").count(), 1
        )
