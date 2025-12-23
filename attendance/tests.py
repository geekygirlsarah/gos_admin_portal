from django.test import TestCase
from django.utils import timezone
from programs.models import Program, Student, ProgramFeature
from .models import KioskDevice, RFIDCard, AttendanceEvent, AttendanceSession
from .services import record_tap, resolve_student_by_uid
from datetime import timedelta


class AttendanceServiceTests(TestCase):
    def setUp(self):
        self.program = Program.objects.create(name="Test Program")
        # Enable attendance feature if needed (services.py checks for 'attendance' feature)
        feat, _ = ProgramFeature.objects.get_or_create(
            key="attendance", defaults={"name": "Attendance"}
        )
        self.program.features.add(feat)

        self.student = Student.objects.create(
            legal_first_name="Test", last_name="Student"
        )
        self.rfid = RFIDCard.objects.create(uid="123456", student=self.student)
        self.kiosk = KioskDevice.objects.create(
            name="Main Kiosk", program=self.program, api_key="test-key"
        )

    def test_resolve_student_by_uid(self):
        resolved = resolve_student_by_uid("123456")
        self.assertEqual(resolved, self.student)

        resolved_none = resolve_student_by_uid("unknown")
        self.assertIsNone(resolved_none)

    def test_record_tap_auto_in_out(self):
        now = timezone.now()
        # First tap -> IN
        evt1 = record_tap(
            program=self.program,
            rfid_uid="123456",
            kiosk=self.kiosk,
            event_type="AUTO",
            occurred_at=now,
        )
        self.assertEqual(evt1.event_type, AttendanceEvent.IN)
        self.assertEqual(AttendanceSession.objects.count(), 1)
        session = AttendanceSession.objects.first()
        self.assertIsNone(session.check_out)
        self.assertEqual(session.opened_by_event, evt1)

        # Second tap -> OUT
        later = now + timedelta(minutes=30)
        evt2 = record_tap(
            program=self.program,
            rfid_uid="123456",
            kiosk=self.kiosk,
            event_type="AUTO",
            occurred_at=later,
        )
        self.assertEqual(evt2.event_type, AttendanceEvent.OUT)
        session.refresh_from_db()
        self.assertEqual(session.check_out, later)
        self.assertEqual(session.duration_minutes, 30)
        self.assertEqual(session.closed_by_event, evt2)

    def test_record_tap_explicit_in_out(self):
        now = timezone.now()
        # Explicit IN
        evt1 = record_tap(
            program=self.program,
            rfid_uid="123456",
            kiosk=self.kiosk,
            event_type="IN",
            occurred_at=now,
        )
        self.assertEqual(evt1.event_type, AttendanceEvent.IN)

        # Explicit OUT
        later = now + timedelta(minutes=45)
        evt2 = record_tap(
            program=self.program,
            rfid_uid="123456",
            kiosk=self.kiosk,
            event_type="OUT",
            occurred_at=later,
        )
        self.assertEqual(evt2.event_type, AttendanceEvent.OUT)

        session = AttendanceSession.objects.get(opened_by_event=evt1)
        self.assertEqual(session.check_out, later)
        self.assertEqual(session.duration_minutes, 45)

    def test_visitor_tap(self):
        now = timezone.now()
        evt = record_tap(
            program=self.program,
            visitor_name="John Doe",
            event_type="IN",
            occurred_at=now,
        )
        self.assertEqual(evt.visitor_name, "John Doe")
        self.assertIsNone(evt.student)

        session = AttendanceSession.objects.get(opened_by_event=evt)
        self.assertEqual(session.visitor_name, "John Doe")

    def test_recompute_duration(self):
        now = timezone.now()
        session = AttendanceSession.objects.create(
            program=self.program,
            student=self.student,
            check_in=now,
            check_out=now + timedelta(hours=1, minutes=15),
        )
        session.recompute_duration()
        self.assertEqual(session.duration_minutes, 75)
        self.assertEqual(session.duration_hm, "1:15")

    def test_attendance_feature_gate(self):
        # Create a program without attendance feature
        prog2 = Program.objects.create(name="No Attendance Prog")
        # record_tap should raise PermissionDenied if 'attendance' feature is missing
        from django.core.exceptions import PermissionDenied

        with self.assertRaises(PermissionDenied):
            record_tap(program=prog2, rfid_uid="123456")
