import json
from datetime import datetime, timedelta
from unittest import mock

from django.contrib.auth.models import Group, User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from attendance.models import AttendanceSession
from programs.models import Adult, Enrollment, Program, RolePermission, Student

from .base import (
    make_lead_mentor_user,
    make_mentor_user,
    make_parent_user,
    make_program,
    make_student,
)


class StudentHoursViewTests(TestCase):
    """Tests for the student/parent attendance hours visualization page."""

    def setUp(self):
        self.client = Client()
        self.program = make_program(
            name="Fall Bot",
            start_date=timezone.now().date() - timedelta(days=60),
        )
        self.student = make_student(preferred_first_name="Alice", last_name="Smith")
        Enrollment.objects.create(
            student=self.student, program=self.program, active=True
        )
        self.url = reverse("student_hours", args=[self.student.pk])

        # Create some attendance sessions
        now = timezone.now()
        for i in range(5):
            check_in = now - timedelta(days=i * 7, hours=3)
            check_out = check_in + timedelta(hours=2)
            AttendanceSession.objects.create(
                program=self.program,
                student=self.student,
                check_in=check_in,
                check_out=check_out,
                duration_minutes=120,
            )

        # Lead mentor user (can see all)
        self.lead_user = make_lead_mentor_user()
        self.client.login(username="lead_mentor", password="password123")  # nosec B106

    def test_lead_mentor_can_access(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Alice Smith")

    def test_student_can_access_own_page(self):
        user = User.objects.create_user(
            username="alice", password="password123"  # nosec B106
        )
        Student.objects.filter(pk=self.student.pk).update(user=user)
        self.student.refresh_from_db()
        self.client.login(username="alice", password="password123")  # nosec B106
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_student_cannot_access_other_student(self):
        other_student = make_student(preferred_first_name="Bob", last_name="Jones")
        other_url = reverse("student_hours", args=[other_student.pk])
        user = User.objects.create_user(
            username="alice2", password="password123"  # nosec B106
        )
        Student.objects.filter(pk=self.student.pk).update(user=user)
        self.client.login(username="alice2", password="password123")  # nosec B106
        response = self.client.get(other_url)
        self.assertEqual(response.status_code, 302)

    def test_parent_can_access_child_page(self):
        parent_user = make_parent_user(username="parent1")
        adult = Adult.objects.get(user=parent_user)
        adult.students.add(self.student)
        self.client.login(username="parent1", password="password123")  # nosec B106
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Alice Smith")

    def test_parent_cannot_access_other_student(self):
        other_student = make_student(preferred_first_name="Carol", last_name="Lee")
        other_url = reverse("student_hours", args=[other_student.pk])
        parent_user = make_parent_user(username="parent2")
        adult = Adult.objects.get(user=parent_user)
        adult.students.add(self.student)
        self.client.login(username="parent2", password="password123")  # nosec B106
        response = self.client.get(other_url)
        self.assertEqual(response.status_code, 302)

    def test_unauthenticated_redirects_to_login(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response.url)

    def test_program_filter(self):
        other_program = make_program(
            name="Spring Bot",
            start_date=timezone.now().date() - timedelta(days=30),
        )
        Enrollment.objects.create(
            student=self.student, program=other_program, active=True
        )
        # Add a session to the other program
        now = timezone.now()
        AttendanceSession.objects.create(
            program=other_program,
            student=self.student,
            check_in=now - timedelta(days=5, hours=2),
            check_out=now - timedelta(days=5),
            duration_minutes=120,
        )

        # Without filter: both programs' sessions
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

        # With filter: only the other program
        url_filtered = f"{self.url}?program_id={other_program.pk}"
        response = self.client.get(url_filtered)
        self.assertEqual(response.status_code, 200)

    def test_stats_displayed(self):
        response = self.client.get(self.url)
        content = response.content.decode()
        self.assertIn("Total Hours", content)
        self.assertIn("Avg Hours / Week", content)
        self.assertIn("Weeks Elapsed", content)

    def test_chart_data_in_template(self):
        response = self.client.get(self.url)
        content = response.content.decode()
        self.assertIn("cumulativeChart", content)
        self.assertIn("var labels", content)
        self.assertIn("var data", content)

    def test_calendar_rendered(self):
        response = self.client.get(self.url)
        content = response.content.decode()
        self.assertIn("cal-grid", content)
        now = timezone.localtime()
        month_names = [
            "",
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ]
        self.assertIn(month_names[now.month], content)

    def test_calendar_defaults_to_local_month_near_utc_boundary(self):
        """The calendar must default to the *local* month, not UTC's.

        Regresses a bug where the page used ``timezone.now()`` (UTC) for the
        default month/year while the calendar is displayed in the local
        timezone (America/New_York). In the first hours of a new month, UTC is
        already in the new month while local time is still the previous one,
        which made the calendar show the wrong month depending on when the
        test ran. We freeze "now" at such a boundary so the assertion is
        deterministic regardless of the real clock.
        """
        # 2026-09-01 00:30 UTC == 2026-08-31 20:30 America/New_York, so UTC is
        # September while local is August — exactly the crossing that used to
        # break the calendar.
        frozen_utc = timezone.make_aware(datetime(2026, 9, 1, 0, 30), timezone.UTC)
        with mock.patch("django.utils.timezone.now", return_value=frozen_utc):
            response = self.client.get(self.url)
        content = response.content.decode()
        self.assertIn("August", content)
        self.assertNotIn("September", content)

    def test_calendar_navigation(self):
        now = timezone.localtime()
        # Go to previous month
        prev_month = now.month - 1 if now.month > 1 else 12
        prev_year = now.year if now.month > 1 else now.year - 1
        url = f"{self.url}?cal_month={prev_month}&cal_year={prev_year}"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        month_names = [
            "",
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ]
        self.assertIn(month_names[prev_month], response.content.decode())

    def test_empty_state(self):
        empty_student = make_student(preferred_first_name="Empty", last_name="Student")
        url = reverse("student_hours", args=[empty_student.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("cumulativeChart", content)

    def test_attendance_disabled_program_excluded(self):
        no_att_program = make_program(name="No Attendance", active=True)
        Enrollment.objects.create(
            student=self.student, program=no_att_program, active=True
        )
        response = self.client.get(self.url)
        content = response.content.decode()
        self.assertNotIn(
            "No Attendance",
            content.split("program_id")[0] if "program_id" in content else content,
        )


class ProgramHoursViewTests(TestCase):
    """Tests for the mentor program attendance dashboard."""

    def setUp(self):
        self.client = Client()
        self.program = make_program(
            name="Fall Bot",
            start_date=timezone.now().date() - timedelta(days=60),
        )
        self.student1 = make_student(preferred_first_name="Alice", last_name="Smith")
        self.student2 = make_student(preferred_first_name="Bob", last_name="Jones")
        Enrollment.objects.create(
            student=self.student1, program=self.program, active=True
        )
        Enrollment.objects.create(
            student=self.student2, program=self.program, active=True
        )
        self.url = reverse("program_hours", args=[self.program.pk])

        # Create sessions
        now = timezone.now()
        for student in [self.student1, self.student2]:
            for i in range(3):
                check_in = now - timedelta(days=i * 7, hours=3)
                check_out = check_in + timedelta(hours=2)
                AttendanceSession.objects.create(
                    program=self.program,
                    student=student,
                    check_in=check_in,
                    check_out=check_out,
                    duration_minutes=120,
                )

        # Mentor user
        self.mentor_user = make_mentor_user()
        self.client.login(username="mentor", password="password123")  # nosec B106

    def test_mentor_can_access(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Fall Bot")

    def test_lead_mentor_can_access(self):
        lead_user = make_lead_mentor_user()
        self.client.login(username="lead_mentor", password="password123")  # nosec B106
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_unauthenticated_redirects_to_login(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response.url)

    def test_attendance_disabled_program(self):
        from programs.models import Program as ProgramModel

        no_att = ProgramModel.objects.create(
            name="No Attendance",
            active=True,
            start_date=timezone.now().date(),
            end_date=timezone.now().date() + timedelta(days=365),
        )
        url = reverse("program_hours", args=[no_att.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

    def test_bar_chart_rendered(self):
        response = self.client.get(self.url)
        content = response.content.decode()
        self.assertIn("studentBarChart", content)
        self.assertIn("var labels", content)

    def test_student_list_displayed(self):
        response = self.client.get(self.url)
        content = response.content.decode()
        self.assertIn("Alice Smith", content)
        self.assertIn("Bob Jones", content)
        self.assertIn("Total Hours", content)

    def test_student_list_sorted_by_hours(self):
        # Give Alice more hours
        now = timezone.now()
        for i in range(5):
            check_in = now - timedelta(days=100 + i, hours=3)
            check_out = check_in + timedelta(hours=2)
            AttendanceSession.objects.create(
                program=self.program,
                student=self.student1,
                check_in=check_in,
                check_out=check_out,
                duration_minutes=120,
            )
        response = self.client.get(self.url)
        content = response.content.decode()
        alice_pos = content.find("Alice Smith")
        bob_pos = content.find("Bob Jones")
        self.assertGreater(alice_pos, -1)
        self.assertGreater(bob_pos, -1)
        self.assertLess(alice_pos, bob_pos)

    def test_view_hours_link_to_student_hours(self):
        response = self.client.get(self.url)
        content = response.content.decode()
        self.assertIn(reverse("student_hours", args=[self.student1.pk]), content)
        self.assertIn(reverse("student_hours", args=[self.student2.pk]), content)

    def test_empty_program(self):
        empty_program = make_program(name="Empty Program", active=True)
        url = reverse("program_hours", args=[empty_program.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("No attendance data", content)

    def test_program_dates_displayed(self):
        response = self.client.get(self.url)
        content = response.content.decode()
        self.assertIn(self.program.start_date.strftime("%b"), content)
