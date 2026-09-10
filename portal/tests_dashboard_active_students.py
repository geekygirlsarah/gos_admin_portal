import datetime

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from programs.models import Enrollment, Program, Student


class DashboardActiveStudentsMetricTests(TestCase):
    """The Lead Mentor "Active Students" metric should count students with an
    active enrollment in a *currently running* program, not every non-graduated
    student in the system.
    """

    def setUp(self):
        self.lead_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.lead_user = User.objects.create_user(
            username="metrics_lead", password="password123"  # nosec B106
        )
        self.lead_user.groups.add(self.lead_group)

        today = timezone.localdate()

        self.running = Program.objects.create(
            name="Running Program",
            active=True,
            start_date=today - datetime.timedelta(days=30),
            end_date=today + datetime.timedelta(days=90),
        )
        self.ended = Program.objects.create(
            name="Ended Program",
            active=True,
            start_date=today - datetime.timedelta(days=120),
            end_date=today - datetime.timedelta(days=30),
        )
        self.upcoming = Program.objects.create(
            name="Upcoming Program",
            active=True,
            start_date=today + datetime.timedelta(days=30),
            end_date=today + datetime.timedelta(days=120),
        )

    def _login_and_get_metric(self):
        self.client.login(username="metrics_lead", password="password123")  # nosec B106
        resp = self.client.get(reverse("profile_dashboard"))
        self.assertEqual(resp.status_code, 200)
        return resp.context["lead_stats"]["active_students_count"]

    @staticmethod
    def _make_student(graduated=False):
        return Student.objects.create(
            legal_first_name="Metric",
            last_name=f"Student{Student.objects.count()}",
            graduation_year=2027,
            graduated=graduated,
        )

    def test_counts_only_students_in_running_programs(self):
        in_running = self._make_student()
        Enrollment.objects.create(student=in_running, program=self.running, active=True)

        in_ended = self._make_student()
        Enrollment.objects.create(student=in_ended, program=self.ended, active=True)

        in_upcoming = self._make_student()
        Enrollment.objects.create(
            student=in_upcoming, program=self.upcoming, active=True
        )

        self.assertEqual(self._login_and_get_metric(), 1)

    def test_excludes_inactive_enrollment_and_graduated_students(self):
        dropped = self._make_student()
        Enrollment.objects.create(student=dropped, program=self.running, active=False)

        graduated = self._make_student(graduated=True)
        Enrollment.objects.create(student=graduated, program=self.running, active=True)

        self.assertEqual(self._login_and_get_metric(), 0)

    def test_excludes_students_with_no_running_enrollment(self):
        no_enrollment = self._make_student()
        self.assertEqual(self._login_and_get_metric(), 0)
        self.assertTrue(no_enrollment.pk)
