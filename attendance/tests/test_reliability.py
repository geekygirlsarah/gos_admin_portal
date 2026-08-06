import json
from datetime import timedelta

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from attendance.kiosk_utils import _cookie_name
from attendance.models import (
    AttendanceEvent,
    AttendanceSession,
    KioskConfig,
    KioskDevice,
    RFIDCard,
)
from attendance.services import (
    auto_in_or_out,
    find_card_by_uid,
    get_attendance_stats,
    record_tap,
    resolve_card_by_uid,
)
from programs.models import Adult, Program, ProgramFeature, Student

from .base import make_program, make_student


class AttendanceServiceReliabilityTests(TestCase):
    def setUp(self):
        self.program = make_program("Service Program")
        self.student = make_student(
            first_name="Service", last_name="Student", graduation_year=2027
        )
        self.other_student = make_student(
            first_name="Other", last_name="Student", graduation_year=2027
        )

    def test_auto_in_or_out_closes_stale_open_session_from_previous_day(self):
        now = timezone.now()
        stale_check_in = now - timedelta(days=1, hours=2)
        stale_session = AttendanceSession.objects.create(
            program=self.program,
            student=self.student,
            check_in=stale_check_in,
            check_out=None,
        )

        event_type, new_session = auto_in_or_out(
            program=self.program,
            student=self.student,
            now=now,
        )

        self.assertEqual(event_type, AttendanceEvent.IN)
        stale_session.refresh_from_db()
        self.assertEqual(stale_session.check_out, stale_check_in + timedelta(hours=1))
        self.assertEqual(stale_session.duration_minutes, 60)
        self.assertIsNone(new_session.check_out)
        self.assertNotEqual(new_session.pk, stale_session.pk)

    def test_get_attendance_stats_without_person_selector_returns_zeroes(self):
        stats = get_attendance_stats(self.program)
        self.assertEqual(stats, {"total_hours": 0, "week_hours": 0})

    def test_record_tap_out_without_open_session_creates_closed_zero_minute_session(
        self,
    ):
        now = timezone.now()

        evt = record_tap(
            program=self.program,
            student=self.student,
            event_type=AttendanceEvent.OUT,
            occurred_at=now,
        )

        self.assertEqual(evt.event_type, AttendanceEvent.OUT)
        sessions = AttendanceSession.objects.filter(
            program=self.program, student=self.student
        )
        self.assertEqual(sessions.count(), 1)
        session = sessions.first()
        self.assertEqual(session.check_in, now)
        self.assertEqual(session.check_out, now)
        self.assertEqual(session.duration_minutes, 0)
        self.assertEqual(session.closed_by_event, evt)

    def test_resolve_card_by_uid_returns_match_when_full_uid_update_conflicts(self):
        card = RFIDCard.objects.create(
            uid="12345", student=self.student, is_active=True
        )
        RFIDCard.objects.create(
            uid="00012345", student=self.other_student, is_active=False
        )

        resolved = resolve_card_by_uid("00012345")

        self.assertEqual(resolved, card)
        card.refresh_from_db()
        self.assertEqual(card.uid, "12345")

    def test_find_card_by_uid_matches_stripped_variant(self):
        card = RFIDCard.objects.create(
            uid="98765", student=self.student, is_active=False
        )
        resolved = find_card_by_uid("00098765")
        self.assertEqual(resolved, card)


class KioskApiReliabilityTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.program = make_program("Kiosk Program")
        self.kiosk = KioskConfig.objects.create(
            label="Main Kiosk", program=self.program
        )
        self.cookie_name = _cookie_name(self.kiosk.pk)
        self.student = make_student(
            first_name="Kiosk", last_name="Student", graduation_year=2027
        )

    def test_kiosk_tap_invalid_json_returns_400(self):
        self.client.cookies[self.cookie_name] = "1"
        response = self.client.post(
            reverse("api_kiosk_tap", args=[self.kiosk.pk]),
            data="{",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "Invalid JSON.")

    def test_kiosk_tap_unknown_student_id_returns_404(self):
        self.client.cookies[self.cookie_name] = "1"
        response = self.client.post(
            reverse("api_kiosk_tap", args=[self.kiosk.pk]),
            data=json.dumps({"student_id": 999999, "event_type": "AUTO"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"], "Student not found.")

    def test_kiosk_tap_unknown_rfid_member_returns_400(self):
        self.client.cookies[self.cookie_name] = "1"
        response = self.client.post(
            reverse("api_kiosk_tap", args=[self.kiosk.pk]),
            data=json.dumps({"rfid_uid": "UNKNOWN-UID", "event_type": "AUTO"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "Member not found.")

    def test_kiosk_lookup_leading_zero_uid_resolves_and_normalizes_card(self):
        card = RFIDCard.objects.create(
            uid="12345", student=self.student, is_active=True
        )
        self.client.cookies[self.cookie_name] = "1"

        response = self.client.get(
            reverse("api_kiosk_lookup", args=[self.kiosk.pk]),
            {"rfid": "00012345"},
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data["students"]), 1)
        self.assertEqual(data["students"][0]["id"], self.student.id)

        card.refresh_from_db()
        self.assertEqual(card.uid, "00012345")

    def test_kiosk_request_code_missing_email_returns_400(self):
        response = self.client.post(
            reverse("api_kiosk_request_code", args=[self.kiosk.pk]),
            data=json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["success"])


class RFIDManagementReliabilityTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_superuser(
            username="admin", email="admin@example.com", password="password"
        )  # nosec B106
        self.client.login(username="admin", password="password")  # nosec B106

        self.student_a = make_student(
            first_name="Student", last_name="A", graduation_year=2027
        )
        self.student_b = make_student(
            first_name="Student", last_name="B", graduation_year=2027
        )

    def test_assign_existing_uid_reassigns_single_card_record(self):
        card = RFIDCard.objects.create(
            uid="12345", student=self.student_b, is_active=True
        )

        response = self.client.post(
            reverse("rfid_management"),
            {
                "action": "assign",
                "person_type": "student",
                "person_id": self.student_a.id,
                "uid": "12345",
            },
        )

        self.assertEqual(response.status_code, 302)
        card.refresh_from_db()
        self.assertEqual(card.student, self.student_a)
        self.assertTrue(card.is_active)
        self.assertEqual(RFIDCard.objects.filter(uid="12345").count(), 1)

    def test_assign_leading_zero_variant_reuses_existing_card(self):
        card = RFIDCard.objects.create(
            uid="12345", student=self.student_b, is_active=True
        )

        response = self.client.post(
            reverse("rfid_management"),
            {
                "action": "assign",
                "person_type": "student",
                "person_id": self.student_a.id,
                "uid": "0012345",
            },
        )

        self.assertEqual(response.status_code, 302)
        card.refresh_from_db()
        self.assertEqual(card.student, self.student_a)
        self.assertEqual(card.uid, "0012345")
        self.assertFalse(RFIDCard.objects.filter(uid="12345").exists())
