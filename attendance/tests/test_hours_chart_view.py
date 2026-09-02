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
        expected_end = timezone.localtime().date().isoformat()
        self.assertIn(f'value="{expected_end}"', content)

    def test_default_dates_program_end_before_today(self):
        past_end = timezone.localtime().date() - timedelta(days=10)
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

    def test_include_unlogged_students_in_chart_and_list(self):
        # student3 is enrolled but has no attendance sessions
        student3 = make_student(preferred_first_name="Charlie", last_name="Brown")
        Enrollment.objects.create(student=student3, program=self.program, active=True)

        url = f"{self.program_url}&include_unlogged=1"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        # Charlie should be in context student_list with 0 hours
        student_list = response.context["student_list"]
        charlie_entry = next(
            (s for s in student_list if s["name"] == "Charlie Brown"), None
        )
        self.assertIsNotNone(charlie_entry)
        self.assertEqual(charlie_entry["total_hours"], 0.0)
        self.assertEqual(charlie_entry["session_count"], 0)
        self.assertIsNone(charlie_entry["last_attended"])
        self.assertEqual(charlie_entry["avg_per_week"], 0.0)

        # Charlie should be in chart labels with 0 in chart data
        chart_labels = json.loads(response.context["chart_labels_json"])
        chart_data = json.loads(response.context["chart_data_json"])
        self.assertIn("Charlie Brown", chart_labels)
        charlie_idx = chart_labels.index("Charlie Brown")
        self.assertEqual(chart_data[charlie_idx], 0.0)
        self.assertEqual(response.context["student_count"], 3)

        # Charlie should be in the rendered HTML
        content = response.content.decode()
        self.assertIn("Charlie Brown", content)

    def test_default_excludes_unlogged_students(self):
        student3 = make_student(preferred_first_name="Charlie", last_name="Brown")
        Enrollment.objects.create(student=student3, program=self.program, active=True)

        response = self.client.get(self.program_url)
        self.assertEqual(response.status_code, 200)

        chart_labels = json.loads(response.context["chart_labels_json"])
        self.assertNotIn("Charlie Brown", chart_labels)
        student_list = response.context["student_list"]
        self.assertFalse(any(s["name"] == "Charlie Brown" for s in student_list))
        self.assertEqual(response.context["student_count"], 2)

    def test_include_unlogged_empty_program_shows_chart_and_table(self):
        empty_program = make_program(name="Zero Sessions Program", active=True)
        student = make_student(preferred_first_name="Dana", last_name="Scully")
        Enrollment.objects.create(student=student, program=empty_program, active=True)

        url = f"{self.url}?program_id={empty_program.pk}&include_unlogged=1"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        self.assertEqual(response.context["student_count"], 1)
        student_list = response.context["student_list"]
        self.assertEqual(student_list[0]["name"], "Dana Scully")
        self.assertEqual(student_list[0]["total_hours"], 0.0)

        content = response.content.decode()
        self.assertIn("Dana Scully", content)
        self.assertNotIn("No attendance data found", content)
        self.assertIn("studentHoursChart", content)

    def test_include_unlogged_ignores_inactive_and_graduated_students(self):
        # Inactive enrollment
        inactive_student = make_student(
            preferred_first_name="Inactive", last_name="User"
        )
        Enrollment.objects.create(
            student=inactive_student, program=self.program, active=False
        )

        # Graduated student
        grad_student = make_student(
            preferred_first_name="Graduated", last_name="Senior", graduated=True
        )
        Enrollment.objects.create(
            student=grad_student, program=self.program, active=True
        )

        url = f"{self.program_url}&include_unlogged=1"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        chart_labels = json.loads(response.context["chart_labels_json"])
        self.assertNotIn("Inactive User", chart_labels)
        self.assertNotIn("Graduated Senior", chart_labels)

    def test_include_unlogged_sort_alpha_and_hours(self):
        student3 = make_student(preferred_first_name="Aaron", last_name="Adams")
        Enrollment.objects.create(student=student3, program=self.program, active=True)

        # Sort alpha: Aaron Adams should be first
        url_alpha = f"{self.program_url}&include_unlogged=1&sort=alpha"
        response = self.client.get(url_alpha)
        student_list = response.context["student_list"]
        chart_labels = json.loads(response.context["chart_labels_json"])
        self.assertEqual(student_list[0]["name"], "Aaron Adams")
        self.assertEqual(chart_labels[0], "Aaron Adams")

        # Sort hours: Aaron Adams (0 hrs) should be last
        url_hours = f"{self.program_url}&include_unlogged=1&sort=hours"
        response = self.client.get(url_hours)
        student_list = response.context["student_list"]
        chart_labels = json.loads(response.context["chart_labels_json"])
        self.assertEqual(student_list[-1]["name"], "Aaron Adams")
        self.assertEqual(chart_labels[-1], "Aaron Adams")

    def test_include_unlogged_preserved_in_sort_urls(self):
        url = f"{self.program_url}&include_unlogged=1"
        response = self.client.get(url)
        self.assertIn("include_unlogged=1", response.context["sort_hours_url"])
        self.assertIn("include_unlogged=1", response.context["sort_alpha_url"])

    def test_include_unlogged_checkbox_in_form(self):
        response = self.client.get(self.program_url)
        content = response.content.decode()
        self.assertIn('name="include_unlogged"', content)
        self.assertNotIn('name="include_unlogged" value="1" checked', content)

        url = f"{self.program_url}&include_unlogged=1"
        response = self.client.get(url)
        content = response.content.decode()
        self.assertIn("checked", content)
