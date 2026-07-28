import json

from django.test import Client, TestCase
from django.utils import timezone

from api.models import ApiClientKey
from attendance.models import AttendanceEvent, AttendanceSession, RFIDCard
from attendance.services import record_tap
from programs.models import Adult, Program, ProgramFeature


class MentorAttendanceTests(TestCase):
    def setUp(self):
        self.program = Program.objects.create(name="Test Program")
        feat, _ = ProgramFeature.objects.get_or_create(
            key="attendance", defaults={"name": "Attendance"}
        )
        self.program.features.add(feat)

        self.mentor = Adult.objects.create(
            first_name="Mentor", last_name="User", is_mentor=True
        )

    def test_mentor_rfid_association(self):
        # This should now SUCCEED
        rfid = RFIDCard.objects.create(uid="M123456", adult=self.mentor)
        self.assertEqual(rfid.adult, self.mentor)

    def test_mentor_lookup_by_rfid(self):
        # Setup mentor and RFID
        mentor = Adult.objects.create(
            first_name="Mentor", last_name="Joe", is_mentor=True
        )
        RFIDCard.objects.create(uid="M-RFID-001", adult=mentor)

        # Test lookup
        client = Client()
        api_key = ApiClientKey.objects.create(
            name="Test Key",
            key="mentorlookuptestkey",  # nosec B106
            scope=ApiClientKey.SCOPE_READ,
        )
        url = "/api/v1/attendance/student/lookup"
        response = client.get(url, {"rfid": "M-RFID-001"}, HTTP_X_API_KEY=api_key.key)

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data["students"]), 1)
        self.assertEqual(data["students"][0]["name"], "Mentor Joe")
        self.assertEqual(data["students"][0]["type"], "mentor")

    def test_mentor_tap_via_api(self):
        mentor = Adult.objects.create(
            first_name="Mentor", last_name="Joe", is_mentor=True
        )
        RFIDCard.objects.create(uid="M-RFID-001", adult=mentor)

        client = Client()
        api_key = ApiClientKey.objects.create(
            name="Test Key",
            key="mentortaptestkey",  # nosec B106
            scope=ApiClientKey.SCOPE_WRITE,
        )
        url = "/api/v1/attendance/tap"
        payload = {
            "program_id": self.program.id,
            "rfid_uid": "M-RFID-001",
            "event_type": "IN",
        }
        response = client.post(
            url,
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_API_KEY=api_key.key,
        )

        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data["adult_id"], mentor.id)
        self.assertIsNone(data["student_id"])
