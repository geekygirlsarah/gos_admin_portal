from datetime import date, time, timedelta

from django.contrib.auth.models import Group, User
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from attendance.models import AttendanceSession
from outreach.models import OutreachEvent, OutreachShift, OutreachSignup
from programs.models import (
    Adult,
    AdultStudentRelationship,
    Enrollment,
    Program,
    ProgramFeature,
    Student,
)

TEST_FILE_KEY = "ZmDfcTF7_60GrrY167zsiPd67pEvs0aGOv2oasOM1Pg="
TEST_TOSEND_KEY = "ts_test_key_12345"


@override_settings(FILE_ENCRYPTION_KEY=TEST_FILE_KEY, TOSEND_API_KEY=TEST_TOSEND_KEY)
class StudentProfileOverviewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.lead_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.lead_user = User.objects.create_superuser(
            username="lead_mentor",
            password="password123",  # nosec B106
            email="lead@example.com",
        )
        self.lead_user.groups.add(self.lead_group)

        self.program = Program.objects.create(
            name="Girls of Steel FRC 2026",
            start_date=date(2026, 1, 1),
            active=True,
        )
        self.attendance_feature, _ = ProgramFeature.objects.get_or_create(
            key="attendance",
            defaults={"name": "Attendance", "display_order": 10},
        )
        self.outreach_feature, _ = ProgramFeature.objects.get_or_create(
            key="outreach",
            defaults={"name": "Outreach", "display_order": 20},
        )
        self.program.features.add(self.attendance_feature, self.outreach_feature)

        self.student = Student.objects.create(
            legal_first_name="Elizabeth",
            preferred_first_name="Beth",
            last_name="Smith",
            personal_email="beth.smith@example.com",
            phone_number="412-555-0199",
            address="123 Innovation Way",
            city="Pittsburgh",
            state="PA",
            zip_code="15213",
            allergies="Peanuts",
            pronouns="she/her",
            tshirt_size="M",
            graduation_year=2027,
        )

        self.parent = Adult.objects.create(
            legal_first_name="Mary",
            last_name="Smith",
            personal_email="mary.smith@example.com",
            phone_number="412-555-0100",
            is_parent=True,
        )
        self.rel = AdultStudentRelationship.objects.create(
            adult=self.parent,
            student=self.student,
            relationship_to_student="mother",
        )
        self.student.primary_contact_relationship = self.rel
        self.student.save()

        self.enrollment = Enrollment.objects.create(
            student=self.student,
            program=self.program,
            active=True,
        )

        # Create attendance sessions
        now = timezone.now()
        AttendanceSession.objects.create(
            student=self.student,
            program=self.program,
            check_in=now - timedelta(hours=3),
            check_out=now,
            duration_minutes=180,
        )

        # Create outreach events, shifts, and signups
        self.event = OutreachEvent.objects.create(
            program=self.program,
            name="Robotics STEM Expo",
            description="Community STEM demonstration",
        )
        self.shift = OutreachShift.objects.create(
            event=self.event,
            date=now.date() + timedelta(days=3),
            start_time=time(10, 0),
            end_time=time(14, 0),
            max_champions=2,
            max_helpers=5,
        )
        self.signup = OutreachSignup.objects.create(
            student=self.student,
            shift=self.shift,
            role=OutreachSignup.CHAMPION,
        )

    def test_student_profile_displays_preferred_name_and_participation_overview(self):
        self.client.force_login(self.lead_user)
        url = reverse("student_detail", args=[self.student.pk])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)

        # Display name (Beth Smith) is prominent
        self.assertContains(response, "Beth Smith")
        # Member since year is visible
        self.assertContains(response, f"Member since {self.student.member_since_year}")
        self.assertContains(response, "Member Since")
        # Attendance hours summary is visible on the default overview
        self.assertContains(response, "Attendance")
        self.assertContains(response, "3.0")  # 180 mins = 3.0 hrs
        self.assertContains(response, reverse("student_hours", args=[self.student.pk]))

        # Outreach stats & upcoming commitments are visible on the default overview
        self.assertContains(response, "Outreach")
        self.assertContains(response, "Robotics STEM Expo")
        self.assertContains(response, "Champion")

        # Personal details section toggle/container is present
        self.assertContains(response, "personalDetailsSection")
        self.assertContains(response, "View Contact &amp; Personal Details")

        # Contact details exist inside the personal details container
        self.assertContains(response, "123 Innovation Way")
        self.assertContains(response, "Mary Smith")

    def test_student_profile_action_buttons_scoped_to_personal_records_card(self):
        self.client.force_login(self.lead_user)
        url = reverse("student_detail", args=[self.student.pk])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()

        # Exactly one "View Contact & Personal Details" button and one "Edit Profile" button
        self.assertEqual(
            content.count("View Contact &amp; Personal Details"),
            1,
            "There should only be one 'View Contact & Personal Details' button on the page.",
        )
        self.assertEqual(
            content.count("Edit Profile"),
            1,
            "There should only be one 'Edit Profile' button on the page.",
        )

    def test_student_list_has_streamlined_view_profile_action(self):
        self.client.force_login(self.lead_user)
        url = reverse("student_list")
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Beth Smith")
        self.assertContains(response, "View Profile")
        self.assertContains(response, reverse("student_detail", args=[self.student.pk]))
