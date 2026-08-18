import datetime

from django.contrib.auth.models import Group, User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from attendance.models import AttendanceEvent, AttendanceSession
from programs.models import Program


class VisitorManagementTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.lead_mentor_user = User.objects.create_superuser(
            username="leadmentor", email="lead@example.com", password="password"
        )  # nosec B106
        self.lead_mentor_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.lead_mentor_user.groups.add(self.lead_mentor_group)
        self.client.login(username="leadmentor", password="password")  # nosec B106

        self.program = Program.objects.create(
            name="Test Program", start_date=datetime.date(2026, 1, 1)
        )
        # Ensure program has attendance feature
        from programs.models import ProgramFeature

        self.attendance_feature, _ = ProgramFeature.objects.get_or_create(
            name="Attendance", key="attendance"
        )
        self.program.features.add(self.attendance_feature)

        # Create some inconsistent visitor sessions
        self.now = timezone.now()
        AttendanceSession.objects.create(
            program=self.program,
            visitor_name="John",
            check_in=self.now - datetime.timedelta(hours=2),
            check_out=self.now - datetime.timedelta(hours=1),
            duration_minutes=60,
        )
        AttendanceSession.objects.create(
            program=self.program,
            visitor_name="John Doe",
            check_in=self.now - datetime.timedelta(hours=4),
            check_out=self.now - datetime.timedelta(hours=3),
            duration_minutes=60,
        )

    def test_edit_visitor_name(self):
        """Verify that we can now edit visitor_name via AllAttendanceView."""
        session = AttendanceSession.objects.get(visitor_name="John")
        url = reverse("all_attendance")

        # Try to update visitor_name
        response = self.client.post(
            url,
            {
                "action": "update",
                "session_id": session.id,
                "visitor_name": "John Fixed",
                "check_in": session.check_in.strftime("%Y-%m-%dT%H:%M"),
                "program_id": self.program.id,
            },
        )

        session.refresh_from_db()
        self.assertEqual(session.visitor_name, "John Fixed")

    def test_visitor_name_inconsistency(self):
        """Verify that we have multiple versions of the same name."""
        names = list(
            AttendanceSession.objects.values_list("visitor_name", flat=True).distinct()
        )
        self.assertIn("John", names)
        self.assertIn("John Doe", names)
        self.assertEqual(len(names), 2)

    def test_merge_visitors(self):
        """Verify that we can merge visitor names."""
        url = reverse("visitor_management")

        # Merge "John" and "John Doe" into "John Doe"
        response = self.client.post(
            url,
            {
                "action": "merge",
                "selected_names": ["John", "John Doe"],
                "target_name": "John Doe",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            AttendanceSession.objects.filter(visitor_name="John Doe").count(), 2
        )
        self.assertEqual(
            AttendanceSession.objects.filter(visitor_name="John").count(), 0
        )

    def test_merge_visitors_events_sync(self):
        """Verify that AttendanceEvent records are also updated during merge."""
        # Create an event for John
        AttendanceEvent.objects.create(
            program=self.program,
            visitor_name="John",
            event_type="IN",
            occurred_at=self.now,
        )

        url = reverse("visitor_management")
        self.client.post(
            url,
            {
                "action": "merge",
                "selected_names": ["John"],
                "target_name": "John Fixed",
            },
        )

        self.assertEqual(
            AttendanceEvent.objects.filter(visitor_name="John Fixed").count(), 1
        )
        self.assertEqual(AttendanceEvent.objects.filter(visitor_name="John").count(), 0)
