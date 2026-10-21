import json

from django.test import Client, TestCase
from django.urls import reverse

from attendance.kiosk_utils import _cookie_name
from attendance.models import KioskConfig, RFIDCard
from attendance.tests.base import make_program
from programs.models import Enrollment, Program, Student


class KioskProgramBoundingReproductionTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.program_current = make_program(name="Current Program 2026")
        self.program_past = make_program(name="Past Program 2024")

        self.kiosk = KioskConfig.objects.create(
            label="Current Kiosk", program=self.program_current
        )
        self.cookie_name = _cookie_name(self.kiosk.pk)
        self.client.cookies[self.cookie_name] = "1"

        # Past student: not enrolled in current program (only in past program), not graduated
        self.past_zoey = Student.objects.create(
            legal_first_name="Zoey",
            preferred_first_name="Zoey",
            last_name="Oldstudent",
            graduated=False,
        )
        Enrollment.objects.create(
            student=self.past_zoey,
            program=self.program_past,
            active=True,
        )
        self.past_zoey_rfid = RFIDCard.objects.create(
            uid="RFID-PAST-ZOEY",
            student=self.past_zoey,
            is_active=True,
        )

        # Current student: enrolled in current program
        self.current_zoey = Student.objects.create(
            legal_first_name="Zoey",
            preferred_first_name="Zoey",
            last_name="Newstudent",
            graduated=False,
        )
        Enrollment.objects.create(
            student=self.current_zoey,
            program=self.program_current,
            active=True,
        )
        self.current_zoey_rfid = RFIDCard.objects.create(
            uid="RFID-CURRENT-ZOEY",
            student=self.current_zoey,
            is_active=True,
        )

    def test_kiosk_lookup_by_name_only_returns_students_in_program(self):
        """Looking up 'Zoey' at current program's kiosk should ONLY return current_zoey, not past_zoey."""
        url = reverse("api_kiosk_lookup", args=[self.kiosk.pk])
        response = self.client.get(url, {"name": "Zoey"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        student_ids = [s["id"] for s in data["students"]]

        self.assertIn(self.current_zoey.pk, student_ids)
        self.assertNotIn(self.past_zoey.pk, student_ids)

    def test_kiosk_lookup_by_rfid_only_returns_students_in_program(self):
        """Looking up past student's RFID at current program's kiosk should return no match."""
        url = reverse("api_kiosk_lookup", args=[self.kiosk.pk])
        response = self.client.get(url, {"rfid": "RFID-PAST-ZOEY"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["students"], [])

    def test_kiosk_tap_by_student_id_rejects_student_not_in_program(self):
        """Tapping with past student's ID should be rejected on current program's kiosk."""
        url = reverse("api_kiosk_tap", args=[self.kiosk.pk])
        response = self.client.post(
            url,
            data=json.dumps({"student_id": self.past_zoey.pk, "event_type": "AUTO"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)

    def test_kiosk_tap_by_rfid_rejects_student_not_in_program(self):
        """Tapping past student's RFID should be rejected on current program's kiosk."""
        url = reverse("api_kiosk_tap", args=[self.kiosk.pk])
        response = self.client.post(
            url,
            data=json.dumps({"rfid_uid": "RFID-PAST-ZOEY", "event_type": "AUTO"}),
            content_type="application/json",
        )
        self.assertIn(response.status_code, [400, 404])
