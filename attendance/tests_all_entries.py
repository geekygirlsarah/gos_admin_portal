from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from attendance.models import AttendanceSession
from programs.models import Program, Student


class AllAttendanceEntriesTests(TestCase):
    def setUp(self):
        self.lead_mentor_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.lead_mentor_user = User.objects.create_user(
            username="lead_mentor", password="password123"
        )
        self.lead_mentor_user.groups.add(self.lead_mentor_group)

        self.mentor_group, _ = Group.objects.get_or_create(name="Mentor")
        self.mentor_user = User.objects.create_user(
            username="mentor", password="password123"
        )
        self.mentor_user.groups.add(self.mentor_group)

        self.program = Program.objects.create(
            name="Test Program", start_date=timezone.now().date()
        )
        # We need to add 'attendance' feature to program if it checks for it
        from programs.models import ProgramFeature

        self.feat, _ = ProgramFeature.objects.get_or_create(
            key="attendance", name="Attendance"
        )
        self.program.features.add(self.feat)

        self.student = Student.objects.create(first_name="Test", last_name="Student")
        self.session = AttendanceSession.objects.create(
            program=self.program, student=self.student, check_in=timezone.now()
        )

    def test_lead_mentor_can_access_all_attendance(self):
        self.client.login(username="lead_mentor", password="password123")
        url = reverse("all_attendance")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "All Program Entries")
        self.assertContains(response, self.student.full_name)

    def test_mentor_cannot_access_all_attendance(self):
        self.client.login(username="mentor", password="password123")
        url = reverse("all_attendance")
        response = self.client.get(url)
        # LeadMentorRequiredMixin redirects to home with error.
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("home"), response.url)

    def test_update_attendance_session(self):
        self.client.login(username="lead_mentor", password="password123")
        url = reverse("all_attendance")
        new_check_in = timezone.now().replace(microsecond=0) - timezone.timedelta(
            hours=1
        )
        # Use localtime for strftime because the view expects local time from datetime-local input
        local_check_in = timezone.localtime(new_check_in)
        response = self.client.post(
            url,
            {
                "action": "update",
                "session_id": self.session.id,
                "check_in": local_check_in.strftime("%Y-%m-%dT%H:%M"),
            },
        )
        self.assertEqual(response.status_code, 302)
        self.session.refresh_from_db()
        self.assertAlmostEqual(
            self.session.check_in, new_check_in, delta=timezone.timedelta(seconds=60)
        )

    def test_delete_attendance_session(self):
        self.client.login(username="lead_mentor", password="password123")
        url = reverse("all_attendance")
        response = self.client.post(
            url,
            {
                "action": "delete",
                "session_id": self.session.id,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(AttendanceSession.objects.filter(id=self.session.id).exists())

    def test_update_program_and_visitor_team(self):
        # Create another program
        program2 = Program.objects.create(
            name="Program 2", start_date=timezone.now().date()
        )
        program2.features.add(self.feat)

        # Create a visitor session
        visitor_session = AttendanceSession.objects.create(
            program=self.program,
            visitor_name="John Doe",
            visitor_team_number=1234,
            check_in=timezone.now(),
        )

        self.client.login(username="lead_mentor", password="password123")
        url = reverse("all_attendance")

        # 1. Update program for the student session
        response = self.client.post(
            url,
            {
                "action": "update",
                "session_id": self.session.id,
                "check_in": timezone.localtime(self.session.check_in).strftime(
                    "%Y-%m-%dT%H:%M"
                ),
                "program_id": program2.id,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.session.refresh_from_db()
        self.assertEqual(self.session.program, program2)

        # 2. Update visitor team number
        response = self.client.post(
            url,
            {
                "action": "update",
                "session_id": visitor_session.id,
                "check_in": timezone.localtime(visitor_session.check_in).strftime(
                    "%Y-%m-%dT%H:%M"
                ),
                "program_id": self.program.id,
                "visitor_team_number": 5678,
            },
        )
        self.assertEqual(response.status_code, 302)
        visitor_session.refresh_from_db()
        self.assertEqual(visitor_session.visitor_team_number, 5678)

    def test_sorting_attendance_sessions(self):
        # Create another student and session
        student2 = Student.objects.create(first_name="Alpha", last_name="Alpha")
        AttendanceSession.objects.create(
            program=self.program,
            student=student2,
            check_in=timezone.now() - timezone.timedelta(days=1),
        )

        self.client.login(username="lead_mentor", password="password123")
        url = reverse("all_attendance")

        # Default sort (check_in desc)
        response = self.client.get(url)
        sessions = list(response.context["sessions"])
        self.assertEqual(sessions[0].student.last_name, "Student")
        self.assertEqual(sessions[1].student.last_name, "Alpha")

        # Sort by person asc
        response = self.client.get(f"{url}?sort=person&dir=asc")
        sessions = list(response.context["sessions"])
        self.assertEqual(sessions[0].student.last_name, "Alpha")
        self.assertEqual(sessions[1].student.last_name, "Student")
