from django.test import TestCase
from django.core.exceptions import ValidationError
from programs.models import Student, School
from outreach.models import OutreachEvent, OutreachSignup

class OutreachModelTest(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Test School")
        self.student = Student.objects.create(
            first_name="Test",
            last_name="Student",
            school=self.school,
            graduation_year=2027
        )
        self.event = OutreachEvent.objects.create(
            name="Test Event",
            location_name="Test Location",
            location_address="123 Test St",
            start_date="2026-09-01",
            start_time="10:00:00",
            end_time="12:00:00",
            max_champions=2,
            max_helpers=5
        )

    def test_event_creation(self):
        self.assertEqual(str(self.event), "Test Event")

    def test_signup_creation(self):
        signup = OutreachSignup.objects.create(
            student=self.student,
            event=self.event,
            role=OutreachSignup.HELPER
        )
        self.assertEqual(str(signup), f"Test Student - Test Event (Helper)")

    def test_unique_signup_per_student_per_event(self):
        OutreachSignup.objects.create(
            student=self.student,
            event=self.event,
            role=OutreachSignup.HELPER
        )
        with self.assertRaises(Exception): # UniqueConstraint should trigger
            OutreachSignup.objects.create(
                student=self.student,
                event=self.event,
                role=OutreachSignup.CHAMPION
            )

    def test_capacity_limit_helpers(self):
        self.event.max_helpers = 1
        self.event.save()
        
        OutreachSignup.objects.create(
            student=self.student,
            event=self.event,
            role=OutreachSignup.HELPER
        )
        
        student2 = Student.objects.create(
            first_name="Test2",
            last_name="Student2",
            school=self.school,
            graduation_year=2027
        )
        
        signup2 = OutreachSignup(
            student=student2,
            event=self.event,
            role=OutreachSignup.HELPER
        )
        with self.assertRaises(ValidationError):
            signup2.clean()
            signup2.save()

    def test_capacity_limit_champions(self):
        self.event.max_champions = 1
        self.event.save()
        
        OutreachSignup.objects.create(
            student=self.student,
            event=self.event,
            role=OutreachSignup.CHAMPION
        )
        
        student2 = Student.objects.create(
            first_name="Test2",
            last_name="Student2",
            school=self.school,
            graduation_year=2027
        )
        
        signup2 = OutreachSignup(
            student=student2,
            event=self.event,
            role=OutreachSignup.CHAMPION
        )
        with self.assertRaises(ValidationError):
            signup2.clean()
            signup2.save()
