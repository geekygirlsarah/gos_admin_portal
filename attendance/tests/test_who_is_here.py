import json

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from attendance.models import AttendanceEvent, AttendanceSession, KioskConfig
from programs.models import Program, ProgramFeature, Student

from .base import make_program, make_student


class KioskWhoIsHereTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.program = make_program()
        self.kiosk = KioskConfig.objects.create(
            label="Who's Here Test Kiosk",
            program=self.program,
        )
        self.student = make_student(
            first_name="Jane", last_name="Doe", graduation_year=2025
        )
        self.client.cookies[f"kiosk_unlocked_{self.kiosk.pk}"] = "1"

    def test_who_is_here_api_locked(self):
        self.client.cookies.clear()
        url = reverse("api_kiosk_who_is_here", args=[self.kiosk.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)

    def test_who_is_here_api_empty(self):
        url = reverse("api_kiosk_who_is_here", args=[self.kiosk.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data["people"], [])

    def test_who_is_here_api_with_data(self):
        AttendanceSession.objects.create(
            program=self.program, student=self.student, check_in=timezone.now()
        )
        AttendanceSession.objects.create(
            program=self.program,
            visitor_name="John Visitor",
            visitor_team_number=1234,
            check_in=timezone.now(),
        )
        closed_student = make_student(
            first_name="Closed", last_name="Student", graduation_year=2025
        )
        AttendanceSession.objects.create(
            program=self.program,
            student=closed_student,
            check_in=timezone.now() - timezone.timedelta(hours=2),
            check_out=timezone.now() - timezone.timedelta(hours=1),
        )

        url = reverse("api_kiosk_who_is_here", args=[self.kiosk.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)

        people = data["people"]
        self.assertEqual(len(people), 2)

        names = [p["name"] for p in people]
        self.assertIn("Jane", names)
        self.assertIn("John Visitor (Team 1234)", names)

    def test_ui_has_who_is_here_elements(self):
        url = reverse("kiosk_signin", args=[self.kiosk.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()

        self.assertIn('id="whoIsHereBtn"', content)
        self.assertIn('id="whoIsHereModal"', content)
