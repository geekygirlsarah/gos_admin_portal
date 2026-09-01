from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from outreach.models import OutreachEvent, OutreachSignup
from outreach.tests.factories import create_outreach_event
from programs.models import Enrollment, Program, ProgramFeature, School, Student


class OutreachChampionPermissionsTest(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Test School")
        self.feature, _ = ProgramFeature.objects.get_or_create(
            key="outreach", defaults={"name": "Outreach"}
        )
        self.program = Program.objects.create(name="Test Program")
        self.program.features.add(self.feature)

        self.student1_user = User.objects.create_user(
            username="student1", password="password"
        )  # nosec B106
        self.student1 = Student.objects.create(
            user=self.student1_user,
            legal_first_name="Alice",
            last_name="Champion",
            school=self.school,
            graduation_year=2027,
        )
        Enrollment.objects.create(
            student=self.student1, program=self.program, active=True
        )

        self.student2_user = User.objects.create_user(
            username="student2", password="password"
        )  # nosec B106
        self.student2 = Student.objects.create(
            user=self.student2_user,
            legal_first_name="Bob",
            last_name="Helper",
            school=self.school,
            graduation_year=2027,
        )
        Enrollment.objects.create(
            student=self.student2, program=self.program, active=True
        )

        self.event = create_outreach_event(
            program=self.program,
            name="Championed Event",
            location_name="Loc 1",
            location_address="Addr 1",
            start_date=timezone.now().date() + timedelta(days=1),
            start_time="10:00:00",
            end_time="12:00:00",
        )
        self.shift = self.event.shifts.first()
        self.shift.max_champions = 1
        self.shift.max_helpers = 5
        self.shift.save()
        OutreachSignup.objects.create(
            student=self.student1, shift=self.shift, role=OutreachSignup.CHAMPION
        )

    def test_champion_can_access_manage_signups(self):
        self.client.login(username="student1", password="password")  # nosec B106
        url = reverse(
            "outreach:shift_manage_signups", args=[self.program.id, self.shift.pk]
        )
        resp = self.client.get(url)
        # Should be accessible because student1 is the champion
        self.assertEqual(resp.status_code, 200)

    def test_other_student_cannot_access_manage_signups(self):
        self.client.login(username="student2", password="password")  # nosec B106
        url = reverse(
            "outreach:shift_manage_signups", args=[self.program.id, self.shift.pk]
        )
        resp = self.client.get(url)
        # Should redirect away because student2 is not a champion of this shift
        self.assertEqual(resp.status_code, 302)

    def test_champion_sees_manage_signups_button(self):
        self.client.login(username="student1", password="password")  # nosec B106
        url = reverse("outreach:event_list", args=[self.program.id])
        resp = self.client.get(url)
        self.assertContains(resp, "Sign-ups")
        self.assertContains(
            resp,
            f'data-url="/programs/{self.program.id}/outreach/shifts/{self.shift.pk}/manage-signups/"',
        )

    def test_helper_does_not_see_manage_signups_button(self):
        OutreachSignup.objects.create(
            student=self.student2, shift=self.shift, role=OutreachSignup.HELPER
        )
        self.client.login(username="student2", password="password")  # nosec B106
        url = reverse("outreach:event_list", args=[self.program.id])
        resp = self.client.get(url)
        self.assertNotContains(resp, "Sign-ups")
