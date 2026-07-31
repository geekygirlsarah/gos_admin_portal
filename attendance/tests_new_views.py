from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from attendance.models import AttendanceSession, KioskConfig
from programs.models import Program, Student


class AttendanceNewViewsTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="mentor", password="password"
        )  # nosec B106
        self.user.is_staff = True
        self.user.is_superuser = True
        self.user.save()

        self.program = Program.objects.create(name="Test Program")
        from programs.models import ProgramFeature

        feature, _ = ProgramFeature.objects.get_or_create(
            key="attendance", defaults={"name": "Attendance"}
        )
        self.program.features.add(feature)

        self.student = Student.objects.create(
            first_name="John", last_name="Doe", graduation_year=2026
        )

        self.session = AttendanceSession.objects.create(
            program=self.program, student=self.student, check_in=timezone.now()
        )

        self.client.login(username="mentor", password="password")  # nosec B106

    def test_active_manifest_view(self):
        response = self.client.get(reverse("attendance_manifest"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "John Doe")
        self.assertContains(response, "Test Program")

    def test_active_manifest_filter(self):
        other_program = Program.objects.create(name="Other Program")
        response = self.client.get(
            reverse("attendance_manifest") + f"?program_id={other_program.id}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "John Doe")

    def test_attendance_summary_view(self):
        # Close the session to record some duration
        self.session.check_out = self.session.check_in + timezone.timedelta(hours=2)
        self.session.recompute_duration()
        self.session.save()

        response = self.client.get(reverse("attendance_summary"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "John Doe")
        self.assertContains(response, "2h 0m")

    def test_rfid_management_view_get(self):
        from attendance.models import RFIDCard

        RFIDCard.objects.create(uid="12345", student=self.student, is_active=True)
        response = self.client.get(reverse("rfid_management"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "RFID Management")
        self.assertContains(response, "12345")
        self.assertContains(response, "John Doe")
        self.assertContains(response, "Currently Assigned RFID Cards")

    def test_rfid_management_search(self):
        response = self.client.get(reverse("rfid_management"), {"q": "John"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "John Doe")

    def test_rfid_management_assign_student(self):
        url = reverse("rfid_management") + "?q=John"
        response = self.client.post(
            url,
            {
                "action": "assign",
                "person_type": "student",
                "person_id": self.student.id,
                "uid": "STUDENT-RFID-123",
            },
        )
        self.assertEqual(response.status_code, 302)
        from attendance.models import RFIDCard

        self.assertTrue(
            RFIDCard.objects.filter(
                uid="STUDENT-RFID-123", student=self.student, is_active=True
            ).exists()
        )

    def test_rfid_management_assign_mentor(self):
        from programs.models import Adult

        mentor = Adult.objects.create(
            first_name="Jane", last_name="Mentor", is_mentor=True
        )
        url = reverse("rfid_management") + "?q=Jane"
        response = self.client.post(
            url,
            {
                "action": "assign",
                "person_type": "mentor",
                "person_id": mentor.id,
                "uid": "MENTOR-RFID-456",
            },
        )
        self.assertEqual(response.status_code, 302)
        from attendance.models import RFIDCard

        self.assertTrue(
            RFIDCard.objects.filter(
                uid="MENTOR-RFID-456", adult=mentor, is_active=True
            ).exists()
        )

    def test_rfid_management_deactivate(self):
        from attendance.models import RFIDCard

        card = RFIDCard.objects.create(uid="OLD-RFID", student=self.student)
        url = reverse("rfid_management") + "?q=John"
        response = self.client.post(url, {"action": "deactivate", "card_id": card.id})
        self.assertEqual(response.status_code, 302)
        card.refresh_from_db()
        self.assertFalse(card.is_active)
