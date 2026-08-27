import json
from datetime import timedelta

from django.contrib.auth.models import User
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


class AttendanceHoursChartViewTests(TestCase):
    """Tests for the mentor attendance hours chart page."""

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
        self.url = reverse("attendance_hours_chart")

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

        self.lead_user = make_lead_mentor_user()
        self.client.login(username="lead_mentor", password="password123")  # nosec B106
        self.program_url = f"{self.url}?program_id={self.program.pk}"

    def test_lead_mentor_can_access(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Attendance Hours Chart")

    def test_mentor_can_access(self):
        make_mentor_user()
        self.client.login(username="mentor", password="password123")  # nosec B106
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

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
            student=self.student1, program=other_program, active=True
        )
        now = timezone.now()
        AttendanceSession.objects.create(
            program=other_program,
            student=self.student1,
            check_in=now - timedelta(days=5, hours=2),
            check_out=now - timedelta(days=5),
            duration_minutes=120,
        )
        url = f"{self.url}?program_id={other_program.pk}"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Spring Bot", content)

    def test_date_range_filter(self):
        now = timezone.now()
        old_check_in = now - timedelta(days=90, hours=3)
        old_check_out = old_check_in + timedelta(hours=2)
        AttendanceSession.objects.create(
            program=self.program,
            student=self.student1,
            check_in=old_check_in,
            check_out=old_check_out,
            duration_minutes=120,
        )
        date_from = (now - timedelta(days=30)).strftime("%Y-%m-%d")
        date_to = now.strftime("%Y-%m-%d")
        url = f"{self.url}?program_id={self.program.pk}&date_from={date_from}&date_to={date_to}"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_chart_rendered(self):
        response = self.client.get(self.url)
        content = response.content.decode()
        self.assertIn("studentHoursChart", content)
        self.assertIn("var labels", content)
        self.assertIn("var data", content)

    def test_student_names_in_chart(self):
        response = self.client.get(self.program_url)
        content = response.content.decode()
        self.assertIn("Alice Smith", content)
        self.assertIn("Bob Jones", content)

    def test_stats_displayed(self):
        response = self.client.get(self.program_url)
        content = response.content.decode()
        self.assertIn("Students", content)
        self.assertIn("Avg Hours / Week", content)
        self.assertIn("Weeks Elapsed", content)
        self.assertIn("Exceeded Averages", content)

    def test_average_lines_in_chart(self):
        response = self.client.get(self.program_url)
        content = response.content.decode()
        self.assertIn("annotation", content)
        self.assertIn("3 hrs/wk", content)
        self.assertIn("6 hrs/wk", content)
        self.assertIn("9 hrs/wk", content)

    def test_custom_average_lines(self):
        from urllib.parse import quote

        avg_lines = quote(json.dumps([{"value": 2, "color": "#ff0000"}]))
        url = f"{self.program_url}&avg_lines={avg_lines}"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("2 hrs/wk", content)

    def test_empty_program(self):
        empty_program = make_program(name="Empty Program", active=True)
        url = f"{self.url}?program_id={empty_program.pk}"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("No attendance data", content)

    def test_no_program_selected_shows_message(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Select a program", content)

    def test_attendance_disabled_program(self):
        no_att = Program.objects.create(
            name="No Attendance",
            active=True,
            start_date=timezone.now().date(),
            end_date=timezone.now().date() + timedelta(days=365),
        )
        url = f"{self.url}?program_id={no_att.pk}"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertNotIn("No Attendance", content.split('name="program_id"')[0])

    def test_student_list_table(self):
        response = self.client.get(self.program_url)
        content = response.content.decode()
        self.assertIn("Alice Smith", content)
        self.assertIn("Bob Jones", content)
        self.assertIn("Sessions", content)

    def test_student_sorted_by_hours(self):
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
        response = self.client.get(self.program_url)
        content = response.content.decode()
        alice_pos = content.find("Alice Smith")
        bob_pos = content.find("Bob Jones")
        self.assertGreater(alice_pos, -1)
        self.assertGreater(bob_pos, -1)
        self.assertLess(alice_pos, bob_pos)

    def test_download_button_present(self):
        response = self.client.get(self.program_url)
        content = response.content.decode()
        self.assertIn("downloadChart", content)
        self.assertIn("Download PNG", content)

    def test_date_inputs_present(self):
        response = self.client.get(self.url)
        content = response.content.decode()
        self.assertIn('name="date_from"', content)
        self.assertIn('name="date_to"', content)

    def test_program_dropdown_present(self):
        response = self.client.get(self.url)
        content = response.content.decode()
        self.assertIn('name="program_id"', content)
        self.assertIn("Fall Bot", content)

    def test_annotation_plugin_loaded(self):
        response = self.client.get(self.url)
        content = response.content.decode()
        self.assertIn("chartjs-plugin-annotation", content)

    def test_view_hours_link(self):
        response = self.client.get(self.program_url)
        content = response.content.decode()
        self.assertIn(reverse("student_hours", args=[self.student1.pk]), content)

    def test_default_dates_from_program(self):
        response = self.client.get(self.program_url)
        content = response.content.decode()
        expected_start = self.program.start_date.isoformat()
        self.assertIn(f'value="{expected_start}"', content)
        expected_end = timezone.now().date().isoformat()
        self.assertIn(f'value="{expected_end}"', content)

    def test_default_dates_program_end_before_today(self):
        past_end = timezone.now().date() - timedelta(days=10)
        self.program.end_date = past_end
        self.program.save()
        response = self.client.get(self.program_url)
        content = response.content.decode()
        self.assertIn(f'value="{past_end.isoformat()}"', content)

    def test_explicit_dates_override_defaults(self):
        url = f"{self.program_url}&date_from=2025-01-01&date_to=2025-06-30"
        response = self.client.get(url)
        content = response.content.decode()
        self.assertIn('value="2025-01-01"', content)
        self.assertIn('value="2025-06-30"', content)

    def test_annotation_line_shows_total(self):
        response = self.client.get(self.program_url)
        content = response.content.decode()
        self.assertIn("hrs/wk =", content)

    def test_preferred_first_name_used(self):
        self.student1.preferred_first_name = "Alicia"
        self.student1.save()
        response = self.client.get(self.program_url)
        content = response.content.decode()
        self.assertIn("Alicia Smith", content)

    def test_fallback_to_legal_first_name(self):
        self.student1.preferred_first_name = ""
        self.student1.save()
        response = self.client.get(self.program_url)
        content = response.content.decode()
        self.assertIn(self.student1.legal_first_name + " Smith", content)

    def test_sort_alpha(self):
        url = f"{self.program_url}&sort=alpha"
        response = self.client.get(url)
        content = response.content.decode()
        alice_pos = content.find("Alice Smith")
        bob_pos = content.find("Bob Jones")
        self.assertGreater(alice_pos, -1)
        self.assertGreater(bob_pos, -1)
        self.assertLess(alice_pos, bob_pos)

    def test_sort_hours_default(self):
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
        response = self.client.get(self.program_url)
        content = response.content.decode()
        alice_pos = content.find("Alice Smith")
        bob_pos = content.find("Bob Jones")
        self.assertLess(alice_pos, bob_pos)

    def test_sort_toggle_buttons_present(self):
        response = self.client.get(self.program_url)
        content = response.content.decode()
        self.assertIn("Sort by Hours", content)
        self.assertIn("A → Z", content)

    def test_weeks_elapsed_uses_date_from(self):
        date_from = (timezone.now().date() - timedelta(days=14)).isoformat()
        date_to = timezone.now().date().isoformat()
        url = (
            f"{self.url}?program_id={self.program.pk}"
            f"&date_from={date_from}&date_to={date_to}"
        )
        response = self.client.get(url)
        content = response.content.decode()
        self.assertIn("2", content)

    def test_exceeded_counts_displayed(self):
        response = self.client.get(self.program_url)
        content = response.content.decode()
        self.assertIn("Exceeded Averages", content)
        self.assertIn("hrs/wk:", content)
