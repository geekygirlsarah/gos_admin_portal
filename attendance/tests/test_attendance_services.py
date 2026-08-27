import datetime
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from attendance.models import AttendanceEvent, AttendanceSession, KioskDevice, RFIDCard
from attendance.services import record_tap, resolve_student_by_uid
from programs.models import Program, ProgramFeature, Student

from .base import make_adult, make_program, make_student


class AttendanceServiceTests(TestCase):
    def setUp(self):
        self.program = make_program()
        self.student = make_student(legal_first_name="Test", last_name="Student")
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
        now = datetime.datetime(2026, 7, 31, 12, 0, 0, tzinfo=datetime.timezone.utc)
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
        now = datetime.datetime(2026, 7, 31, 12, 0, 0, tzinfo=datetime.timezone.utc)
        evt1 = record_tap(
            program=self.program,
            rfid_uid="123456",
            kiosk=self.kiosk,
            event_type="IN",
            occurred_at=now,
        )
        self.assertEqual(evt1.event_type, AttendanceEvent.IN)

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
        now = datetime.datetime(2026, 7, 31, 12, 0, 0, tzinfo=datetime.timezone.utc)
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

    def test_visitor_tap_with_team_number(self):
        now = datetime.datetime(2026, 7, 31, 12, 0, 0, tzinfo=datetime.timezone.utc)
        evt = record_tap(
            program=self.program,
            visitor_name="Jane Smith",
            visitor_team_number=1234,
            event_type="IN",
            occurred_at=now,
        )
        self.assertEqual(evt.visitor_name, "Jane Smith")
        self.assertEqual(evt.visitor_team_number, 1234)

        session = AttendanceSession.objects.get(opened_by_event=evt)
        self.assertEqual(session.visitor_team_number, 1234)

    def test_visitor_tap_without_team_number(self):
        now = datetime.datetime(2026, 7, 31, 12, 0, 0, tzinfo=datetime.timezone.utc)
        evt = record_tap(
            program=self.program,
            visitor_name="No Team",
            event_type="IN",
            occurred_at=now,
        )
        self.assertIsNone(evt.visitor_team_number)

    def test_recompute_duration(self):
        now = datetime.datetime(2026, 7, 31, 12, 0, 0, tzinfo=datetime.timezone.utc)
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
        prog2 = Program.objects.create(name="No Attendance Prog")
        from django.core.exceptions import PermissionDenied

        with self.assertRaises(PermissionDenied):
            record_tap(program=prog2, rfid_uid="123456")


class AttendanceSessionIndexTests(TestCase):
    def test_attendance_session_has_open_session_partial_indexes(self):
        indexes = {index.name: index for index in AttendanceSession._meta.indexes}

        self.assertIn("att_sess_open_student_idx", indexes)
        student_index = indexes["att_sess_open_student_idx"]
        self.assertEqual(student_index.fields, ["program", "student", "check_in"])
        self.assertEqual(
            student_index.condition.children, [("check_out__isnull", True)]
        )

        self.assertIn("att_sess_open_adult_idx", indexes)
        adult_index = indexes["att_sess_open_adult_idx"]
        self.assertEqual(adult_index.fields, ["program", "adult", "check_in"])
        self.assertEqual(adult_index.condition.children, [("check_out__isnull", True)])

    def test_attendance_session_has_visitor_lookup_index(self):
        indexes = {index.name: index for index in AttendanceSession._meta.indexes}

        self.assertIn("att_sess_prog_visitor_in_idx", indexes)
        self.assertEqual(
            indexes["att_sess_prog_visitor_in_idx"].fields,
            ["program", "visitor_name", "check_in"],
        )


class AttendanceModelReliabilityTests(TestCase):
    def setUp(self):
        self.program = make_program("Model Program")
        self.student = make_student(preferred_first_name="Model", last_name="Student")
        self.adult = make_adult(
            legal_first_name="Model", last_name="Mentor", is_mentor=True
        )

    def test_rfid_card_owner_constraint_requires_exactly_one_owner(self):
        from django.db import IntegrityError, transaction

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                RFIDCard.objects.create(
                    uid="BAD-BOTH", student=self.student, adult=self.adult
                )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                RFIDCard.objects.create(uid="BAD-NONE")

    def test_kiosk_device_string_includes_location_when_present(self):
        device = KioskDevice.objects.create(
            name="Front Desk", program=self.program, api_key="key-1", location="Lobby"
        )
        self.assertEqual(str(device), "Front Desk (Lobby)")

    def test_attendance_session_duration_hm_is_zero_padded(self):
        session = AttendanceSession.objects.create(
            program=self.program,
            student=self.student,
            check_in=timezone.now(),
            check_out=timezone.now(),
            duration_minutes=125,
        )
        self.assertEqual(session.duration_hm, "2:05")


class MentorAttendanceTests(TestCase):
    def setUp(self):
        self.program = make_program()
        self.mentor = make_adult(
            legal_first_name="Mentor", last_name="User", is_mentor=True
        )

    def test_mentor_rfid_association(self):
        rfid = RFIDCard.objects.create(uid="M123456", adult=self.mentor)
        self.assertEqual(rfid.adult, self.mentor)
