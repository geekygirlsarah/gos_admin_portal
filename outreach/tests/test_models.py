from django.core.exceptions import ValidationError
from django.test import TestCase

from outreach.models import OutreachEvent, OutreachShift, OutreachSignup
from outreach.tests.factories import create_outreach_event
from programs.models import School, Student


class OutreachModelTest(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Test School")
        self.student = Student.objects.create(
            first_name="Test",
            last_name="Student",
            school=self.school,
            graduation_year=2027,
        )
        self.event = create_outreach_event(
            name="Test Event",
            location_name="Test Location",
            location_address="123 Test St",
            start_date="2026-09-01",
            start_time="10:00:00",
            end_time="12:00:00",
        )
        self.shift = self.event.shifts.first()
        self.shift.max_champions = 2
        self.shift.max_helpers = 5
        self.shift.save()

    def test_event_creation(self):
        self.assertEqual(str(self.event), "Test Event")

    def test_signup_creation(self):
        signup = OutreachSignup.objects.create(
            student=self.student, shift=self.shift, role=OutreachSignup.HELPER
        )
        self.assertEqual(str(signup), f"Test Student - Test Event (Helper)")

    def test_unique_signup_per_student_per_shift(self):
        OutreachSignup.objects.create(
            student=self.student, shift=self.shift, role=OutreachSignup.HELPER
        )
        with self.assertRaises(Exception):  # UniqueConstraint should trigger
            OutreachSignup.objects.create(
                student=self.student, shift=self.shift, role=OutreachSignup.CHAMPION
            )

    def test_capacity_limit_helpers(self):
        self.shift.max_helpers = 1
        self.shift.save()

        OutreachSignup.objects.create(
            student=self.student, shift=self.shift, role=OutreachSignup.HELPER
        )

        student2 = Student.objects.create(
            first_name="Test2",
            last_name="Student2",
            school=self.school,
            graduation_year=2027,
        )

        signup2 = OutreachSignup(
            student=student2, shift=self.shift, role=OutreachSignup.HELPER
        )
        with self.assertRaises(ValidationError):
            signup2.clean()
            signup2.save()

    def test_capacity_limit_champions(self):
        self.shift.max_champions = 1
        self.shift.save()

        OutreachSignup.objects.create(
            student=self.student, shift=self.shift, role=OutreachSignup.CHAMPION
        )

        student2 = Student.objects.create(
            first_name="Test2",
            last_name="Student2",
            school=self.school,
            graduation_year=2027,
        )

        signup2 = OutreachSignup(
            student=student2, shift=self.shift, role=OutreachSignup.CHAMPION
        )
        with self.assertRaises(ValidationError):
            signup2.clean()
            signup2.save()


class OutreachShiftTest(TestCase):
    def setUp(self):
        self.event = OutreachEvent.objects.create(
            name="Multi-Shift Event",
            location_name="Test Location",
            location_address="123 Test St",
        )

    def test_event_without_shifts_has_no_dates(self):
        self.assertIsNone(self.event.start_date)
        self.assertIsNone(self.event.start_time)
        self.assertIsNone(self.event.end_date)
        self.assertIsNone(self.event.end_time)
        self.assertFalse(self.event.is_past)
        self.assertEqual(self.event.duration_hours, 0)

    def test_event_spans_first_to_last_shift(self):
        OutreachShift.objects.create(
            event=self.event,
            date="2026-09-05",
            start_time="09:00:00",
            end_time="12:00:00",
        )
        OutreachShift.objects.create(
            event=self.event,
            date="2026-09-01",
            start_time="13:00:00",
            end_time="17:00:00",
        )

        # Shifts are ordered chronologically regardless of creation order
        self.assertEqual(str(self.event.start_date), "2026-09-01")
        self.assertEqual(str(self.event.start_time), "13:00:00")
        self.assertEqual(str(self.event.end_date), "2026-09-05")
        self.assertEqual(str(self.event.end_time), "12:00:00")

    def test_duration_hours_sums_all_shifts(self):
        OutreachShift.objects.create(
            event=self.event,
            date="2026-09-01",
            start_time="09:00:00",
            end_time="12:00:00",
        )
        OutreachShift.objects.create(
            event=self.event,
            date="2026-09-01",
            start_time="13:00:00",
            end_time="16:00:00",
        )
        self.assertEqual(self.event.duration_hours, 6.0)

    def test_shift_clean_rejects_end_before_start(self):
        shift = OutreachShift(
            event=self.event,
            date="2026-09-01",
            start_time="12:00:00",
            end_time="10:00:00",
        )
        with self.assertRaises(ValidationError):
            shift.clean()
