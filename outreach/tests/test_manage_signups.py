from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from outreach.models import OutreachEvent, OutreachSignup
from outreach.tests.factories import create_outreach_event
from programs.models import Adult, Enrollment, Program, ProgramFeature, School, Student


class OutreachManageSignupsTest(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Test School")
        self.feature, _ = ProgramFeature.objects.get_or_create(
            key="outreach", defaults={"name": "Outreach"}
        )
        self.program = Program.objects.create(name="Test Program")
        self.program.features.add(self.feature)

        self.mentor_user = User.objects.create_user(
            username="mentor", password="password"
        )  # nosec B106
        self.mentor_adult = Adult.objects.create(
            user=self.mentor_user, is_mentor=True, mentor_active=True
        )

        self.student1_user = User.objects.create_user(
            username="student1", password="password"
        )  # nosec B106
        self.student1 = Student.objects.create(
            user=self.student1_user,
            legal_first_name="Student",
            last_name="One",
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
            legal_first_name="Student",
            last_name="Two",
            school=self.school,
            graduation_year=2027,
        )
        Enrollment.objects.create(
            student=self.student2, program=self.program, active=True
        )

        self.event = create_outreach_event(
            program=self.program,
            name="Test Event",
            location_name="Test Location",
            location_address="123 Test St",
            start_date="2026-09-01",
            start_time="10:00:00",
            end_time="12:00:00",
        )
        self.shift = self.event.shifts.first()
        self.shift.max_champions = 2
        self.shift.max_helpers = 5
        self.shift.save()

    def test_manage_signups_view_accessible_to_mentor(self):
        self.client.login(username="mentor", password="password")  # nosec B106
        url = reverse(
            "outreach:shift_manage_signups", args=[self.program.id, self.shift.pk]
        )
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

    def test_event_list_includes_dual_listbox_assets(self):
        self.client.login(username="mentor", password="password")  # nosec B106
        url = reverse("outreach:event_list", args=[self.program.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "js/dual-listbox.js")
        self.assertContains(resp, "css/dual-listbox.css")

    def test_manage_signups_view_accessible_to_shift_champion(self):
        OutreachSignup.objects.create(
            shift=self.shift, student=self.student1, role=OutreachSignup.CHAMPION
        )
        self.client.login(username="student1", password="password")  # nosec B106
        url = reverse(
            "outreach:shift_manage_signups", args=[self.program.id, self.shift.pk]
        )
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

    def test_manage_signups_view_forbidden_for_non_champion_student(self):
        self.client.login(username="student1", password="password")  # nosec B106
        url = reverse(
            "outreach:shift_manage_signups", args=[self.program.id, self.shift.pk]
        )
        resp = self.client.get(url)
        # student1 is not a champion of this shift, so access is denied
        self.assertEqual(resp.status_code, 302)

    def test_mentor_can_add_students(self):
        self.client.login(username="mentor", password="password")  # nosec B106
        url = reverse(
            "outreach:shift_manage_signups", args=[self.program.id, self.shift.pk]
        )

        # Add student1 as champion and student2 as helper
        data = {"champions": [self.student1.pk], "helpers": [self.student2.pk]}
        resp = self.client.post(url, data)
        self.assertEqual(resp.status_code, 302)

        self.assertTrue(
            OutreachSignup.objects.filter(
                shift=self.shift, student=self.student1, role=OutreachSignup.CHAMPION
            ).exists()
        )
        self.assertTrue(
            OutreachSignup.objects.filter(
                shift=self.shift, student=self.student2, role=OutreachSignup.HELPER
            ).exists()
        )

    def test_mentor_can_remove_students(self):
        OutreachSignup.objects.create(
            shift=self.shift, student=self.student1, role=OutreachSignup.CHAMPION
        )

        self.client.login(username="mentor", password="password")  # nosec B106
        url = reverse(
            "outreach:shift_manage_signups", args=[self.program.id, self.shift.pk]
        )

        # Post empty lists should remove all
        data = {"champions": [], "helpers": []}
        resp = self.client.post(url, data)
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(OutreachSignup.objects.filter(shift=self.shift).exists())

    def test_validation_cannot_be_both(self):
        self.client.login(username="mentor", password="password")  # nosec B106
        url = reverse(
            "outreach:shift_manage_signups", args=[self.program.id, self.shift.pk]
        )

        data = {"champions": [self.student1.pk], "helpers": [self.student1.pk]}
        resp = self.client.post(url, data)
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(OutreachSignup.objects.filter(shift=self.shift).exists())

    def test_validation_exceed_limits(self):
        self.client.login(username="mentor", password="password")  # nosec B106
        url = reverse(
            "outreach:shift_manage_signups", args=[self.program.id, self.shift.pk]
        )

        # shift.max_champions is 2. Let's create another student.
        student3_user = User.objects.create_user(
            username="student3", password="password"
        )  # nosec B106
        student3 = Student.objects.create(
            user=student3_user,
            legal_first_name="S3",
            last_name="T",
            school=self.school,
            graduation_year=2027,
        )
        Enrollment.objects.create(student=student3, program=self.program, active=True)

        data = {
            "champions": [self.student1.pk, self.student2.pk, student3.pk],
            "helpers": [],
        }
        resp = self.client.post(url, data)
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(OutreachSignup.objects.filter(shift=self.shift).exists())
