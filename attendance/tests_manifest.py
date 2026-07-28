import json

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from attendance.models import AttendanceEvent, AttendanceSession, KioskConfig
from programs.models import Program, ProgramFeature, Student


class KioskManifestTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.program = Program.objects.create(name="Test Program")
        feat, _ = ProgramFeature.objects.get_or_create(
            key="attendance", defaults={"name": "Attendance"}
        )
        self.program.features.add(feat)
        self.kiosk = KioskConfig.objects.create(
            label="Manifest Test Kiosk",
            program=self.program,
        )
        self.student = Student.objects.create(
            first_name="Jane",
            last_name="Doe",
            graduation_year=2025,
        )
        # Unlock the kiosk via cookie
        self.client.cookies[f"kiosk_unlocked_{self.kiosk.pk}"] = "1"

    def test_manifest_api_locked(self):
        """Manifest API should return 403 if kiosk is locked."""
        self.client.cookies.clear()
        url = reverse("api_kiosk_manifest", args=[self.kiosk.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)

    def test_manifest_api_empty(self):
        """Manifest API should return empty list if no one is signed in."""
        url = reverse("api_kiosk_manifest", args=[self.kiosk.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data["manifest"], [])

    def test_manifest_api_with_data(self):
        """Manifest API should return currently signed-in people."""
        # Create an open session
        AttendanceSession.objects.create(
            program=self.program, student=self.student, check_in=timezone.now()
        )
        # Create a visitor session
        AttendanceSession.objects.create(
            program=self.program,
            visitor_name="John Visitor",
            visitor_team_number=1234,
            check_in=timezone.now(),
        )
        # Create a closed session (should not appear)
        closed_student = Student.objects.create(
            first_name="Closed", last_name="Student", graduation_year=2025
        )
        AttendanceSession.objects.create(
            program=self.program,
            student=closed_student,
            check_in=timezone.now() - timezone.timedelta(hours=2),
            check_out=timezone.now() - timezone.timedelta(hours=1),
        )

        url = reverse("api_kiosk_manifest", args=[self.kiosk.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)

        manifest = data["manifest"]
        self.assertEqual(len(manifest), 2)

        names = [m["name"] for m in manifest]
        self.assertIn("Jane Doe", names)
        self.assertIn("John Visitor (Team 1234)", names)

    def test_ui_has_manifest_elements(self):
        """The kiosk page should have a manifest button and a modal."""
        url = reverse("kiosk_signin", args=[self.kiosk.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()

        # Check for button (or at least the ID we plan to use)
        self.assertIn('id="manifestBtn"', content)
        # Check for modal
        self.assertIn('id="manifestModal"', content)
