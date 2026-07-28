import json

from django.test import Client, TestCase
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
