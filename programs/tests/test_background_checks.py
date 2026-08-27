import datetime
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase

from programs.models import (
    Adult,
    BackgroundCheck,
    BackgroundCheckType,
    Enrollment,
    Program,
    Student,
    timezone,
)


class BackgroundCheckModelTests(TestCase):
    def test_clean_requires_exactly_one_holder(self):
        student = Student.objects.create(legal_first_name="A", last_name="B")
        with self.assertRaises(ValidationError):
            BackgroundCheck(
                check_type=BackgroundCheckType.FBI,
                cleared=True,
            ).clean()
        with self.assertRaises(ValidationError):
            BackgroundCheck(
                student=student,
                adult=Adult.objects.create(legal_first_name="C", last_name="D"),
                check_type=BackgroundCheckType.FBI,
                cleared=True,
            ).clean()
        # Exactly one holder is fine.
        BackgroundCheck(
            student=student,
            check_type=BackgroundCheckType.FBI,
            cleared=True,
        ).clean()

    def test_is_valid(self):
        student = Student.objects.create(
            legal_first_name="A", last_name="B", date_of_birth=datetime.date(2000, 1, 1)
        )
        # Not cleared → invalid.
        check = BackgroundCheck.objects.create(
            student=student, check_type=BackgroundCheckType.FBI, cleared=False
        )
        self.assertFalse(check.is_valid)

        # Cleared with no obtained date → valid (unknown expiry counts as valid).
        check.cleared = True
        check.save()
        self.assertTrue(check.is_valid)

        # Cleared but expired (obtained more than 5 years ago) → invalid.
        check.obtained_date = datetime.date.today() - datetime.timedelta(
            days=365 * 5 + 30
        )
        check.save()
        self.assertFalse(check.is_valid)

        # Cleared, obtained recently (expires in the future) → valid.
        check.obtained_date = datetime.date.today() - datetime.timedelta(days=30)
        check.save()
        self.assertTrue(check.is_valid)

    def test_expiration_date_derived_from_obtained_date(self):
        student = Student.objects.create(
            legal_first_name="A", last_name="B", date_of_birth=datetime.date(2000, 1, 1)
        )
        check = BackgroundCheck.objects.create(
            student=student,
            check_type=BackgroundCheckType.FBI,
            cleared=True,
            obtained_date=datetime.date(2021, 6, 15),
        )
        self.assertEqual(check.expiration_date, datetime.date(2026, 6, 15))
        check.obtained_date = None
        self.assertIsNone(check.expiration_date)

    def test_background_checks_related_names(self):
        student = Student.objects.create(
            legal_first_name="A", last_name="B", date_of_birth=datetime.date(2000, 1, 1)
        )
        adult = Adult.objects.create(legal_first_name="C", last_name="D")
        bc_s = BackgroundCheck.objects.create(
            student=student, check_type=BackgroundCheckType.FBI, cleared=True
        )
        bc_a = BackgroundCheck.objects.create(
            adult=adult, check_type=BackgroundCheckType.FBI, cleared=True
        )
        self.assertIn(bc_s, student.background_checks.all())
        self.assertIn(bc_a, adult.background_checks.all())


class StudentBackgroundCheckRequirementTests(TestCase):
    def _student(self, dob):
        return Student.objects.create(
            legal_first_name="A", last_name="B", date_of_birth=dob
        )

    def test_requires_background_check_without_dob(self):
        # date_of_birth is required on the model, but guard against None.
        with patch.object(
            timezone, "localdate", return_value=datetime.date(2026, 8, 1)
        ):
            s = self._student(datetime.date(2008, 8, 1))
            s.date_of_birth = None
            self.assertFalse(s.requires_background_check())

    def test_needs_background_check_returns_false_when_not_required(self):
        # Born 2012 → way under 17.
        with patch.object(
            timezone, "localdate", return_value=datetime.date(2026, 8, 1)
        ):
            s = self._student(datetime.date(2012, 1, 1))
            self.assertFalse(s.requires_background_check())
            self.assertFalse(s.needs_background_check())

    def test_needs_background_check_missing_types(self):
        with patch.object(
            timezone, "localdate", return_value=datetime.date(2026, 8, 1)
        ):
            s = self._student(datetime.date(2008, 8, 1))
            self.assertTrue(s.needs_background_check())
            # Provide only one → still needs.
            BackgroundCheck.objects.create(
                student=s, check_type=BackgroundCheckType.FBI, cleared=True
            )
            self.assertTrue(s.needs_background_check())
            # Provide all three → satisfied.
            for check_type in BackgroundCheckType.values:
                BackgroundCheck.objects.update_or_create(
                    student=s,
                    check_type=check_type,
                    defaults={
                        "cleared": True,
                        "obtained_date": datetime.date(2022, 1, 1),
                    },
                )
            self.assertFalse(s.needs_background_check())
            # Expire one → needs again.
            expired = BackgroundCheck.objects.get(
                student=s, check_type=BackgroundCheckType.STATE_POLICE
            )
            expired.obtained_date = datetime.date(2019, 1, 1)
            expired.save()
            self.assertTrue(s.needs_background_check())


class EnrollmentClearanceDueSignalTests(TestCase):
    def _student(self, dob):
        return Student.objects.create(
            legal_first_name="A", last_name="B", date_of_birth=dob
        )

    def test_enrollment_sets_clearance_due_for_required_student(self):
        program = Program.objects.create(name="Season")
        with patch.object(
            timezone, "localdate", return_value=datetime.date(2026, 8, 1)
        ):
            student = self._student(datetime.date(2008, 8, 1))
            enrollment = Enrollment.objects.create(student=student, program=program)
            enrollment.refresh_from_db()
            self.assertTrue(enrollment.clearance_due)

    def test_enrollment_clears_clearance_due_for_satisfied_student(self):
        program = Program.objects.create(name="Season")
        with patch.object(
            timezone, "localdate", return_value=datetime.date(2026, 8, 1)
        ):
            student = self._student(datetime.date(2008, 8, 1))
            for check_type in BackgroundCheckType.values:
                BackgroundCheck.objects.create(
                    student=student,
                    check_type=check_type,
                    cleared=True,
                    obtained_date=datetime.date(2022, 1, 1),
                )
            enrollment = Enrollment.objects.create(student=student, program=program)
            enrollment.refresh_from_db()
            self.assertFalse(enrollment.clearance_due)

    def test_enrollment_not_flag_when_student_underage(self):
        program = Program.objects.create(name="Season")
        with patch.object(
            timezone, "localdate", return_value=datetime.date(2026, 8, 1)
        ):
            student = self._student(datetime.date(2012, 1, 1))
            enrollment = Enrollment.objects.create(student=student, program=program)
            enrollment.refresh_from_db()
            self.assertFalse(enrollment.clearance_due)
