import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from attendance.models import StudentPresence
from orders.models import OrderItem
from programs.models import (
    Adult,
    AdultStudentRelationship,
    Crew,
    Enrollment,
    Program,
    ProgramFeature,
    Student,
    SubTeam,
    Team,
)


@override_settings(FILE_ENCRYPTION_KEY="ZmDfcTF7_60GrrY167zsiPd67pEvs0aGOv2oasOM1Pg=")
class ProgramDetailKPITests(TestCase):
    def setUp(self):
        password = "password123"  # nosec B105
        # Lead Mentor
        self.lead_user = User.objects.create_user(
            username="lead_user", password=password
        )
        lm_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.lead_user.groups.add(lm_group)

        # Regular Mentor
        self.mentor_user = User.objects.create_user(
            username="mentor_user", password=password
        )
        self.mentor_adult = Adult.objects.create(
            user=self.mentor_user,
            legal_first_name="Mentor",
            last_name="Person",
            is_mentor=True,
        )

        # Parent & Student
        self.parent_user = User.objects.create_user(
            username="parent_user", password=password
        )
        self.parent_adult = Adult.objects.create(
            user=self.parent_user,
            legal_first_name="Parent",
            last_name="Guardian",
            is_parent=True,
        )
        self.student = Student.objects.create(
            legal_first_name="Jane",
            last_name="Doe",
            date_of_birth=datetime.date(2008, 5, 10),
        )
        AdultStudentRelationship.objects.create(
            adult=self.parent_adult,
            student=self.student,
            relationship_to_student="parent",
        )

        # Program with features
        self.program = Program.objects.create(
            name="Robotics Flagship",
            active=True,
            start_date=datetime.date(2026, 1, 1),
            end_date=datetime.date(2026, 12, 31),
        )
        self.f_bg, _ = ProgramFeature.objects.get_or_create(
            key="background-checks", defaults={"name": "Background Checks"}
        )
        self.f_orders, _ = ProgramFeature.objects.get_or_create(
            key="orders", defaults={"name": "Order Requests"}
        )
        self.f_signout, _ = ProgramFeature.objects.get_or_create(
            key="signout-sheet", defaults={"name": "Sign-out Sheet"}
        )
        self.program.features.add(self.f_bg, self.f_orders, self.f_signout)

        self.team = Team.objects.create(
            team_type="FRC",
            number=3504,
            name="Girls of Steel",
            color="#e91e63",
        )
        self.enrollment = Enrollment.objects.create(
            student=self.student,
            program=self.program,
            active=True,
            team=self.team,
        )
        self.url = reverse("program_detail", args=[self.program.pk])

    def test_health_warning_callout_rendered_when_medical_info_exists(self):
        # Set allergies on student
        self.student.allergies = "Peanut allergy"
        self.student.save()

        # Both Lead Mentor and Regular Mentor should see the health callout
        for username in ("lead_user", "mentor_user"):
            self.client.login(username=username, password="password123")  # nosec B106
            resp = self.client.get(self.url)
            self.assertEqual(resp.status_code, 200)
            self.assertContains(resp, "Health &amp; Dietary Notes on File")
            self.assertContains(resp, "1 enrolled student")
            self.assertContains(
                resp, reverse("program_medical_info", args=[self.program.pk])
            )

    def test_health_warning_callout_hidden_when_no_medical_info(self):
        self.student.allergies = ""
        self.student.dietary_restrictions = ""
        self.student.medical_notes = ""
        self.student.save()

        self.client.login(username="mentor_user", password="password123")  # nosec B106
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Health &amp; Dietary Notes on File")

    def test_priority_1_pending_orders_card_takes_precedence(self):
        # Create unassigned pending order item for this program
        OrderItem.objects.create(
            program=self.program,
            item_name="Aluminium Tube 1x1",
            quantity=Decimal("2"),
            unit_price=Decimal("15.50"),
            requested_by=self.lead_user,
        )
        self.client.login(username="mentor_user", password="password123")  # nosec B106
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)

        self.assertContains(resp, "Pending Order Requests")
        self.assertContains(resp, "1 Item Pending")
        self.assertContains(resp, "$31.00")
        # Subteam Placement card should NOT be shown because Orders has higher priority
        self.assertNotContains(resp, "Roster Assignments")

    def test_priority_2_team_assignments_card_when_no_pending_orders(self):
        # No pending orders exist, but teams/crews exist
        self.client.login(username="mentor_user", password="password123")  # nosec B106
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)

        self.assertContains(resp, "Roster Assignments")
        self.assertContains(resp, "1 / 1 Assigned")
        self.assertNotContains(resp, "Pending Order Requests")

    def test_priority_3_attendance_card_when_no_orders_and_no_teams(self):
        # Remove team from enrollment and remove all teams/crews/subteams
        self.enrollment.team = None
        self.enrollment.save()
        Team.objects.all().delete()
        Crew.objects.all().delete()
        SubTeam.objects.all().delete()

        # Mark presence today
        StudentPresence.objects.create(
            program=self.program,
            student=self.student,
            date=timezone.localdate(),
            status=StudentPresence.PRESENT,
        )

        self.client.login(username="mentor_user", password="password123")  # nosec B106
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)

        self.assertContains(resp, "Today's Attendance")
        self.assertContains(resp, "1 Present Today")
        self.assertNotContains(resp, "Roster Assignments")
        self.assertNotContains(resp, "Pending Order Requests")

    def test_priority_4_program_status_card_when_no_orders_teams_or_attendance(self):
        # Clear teams and remove attendance features
        self.enrollment.team = None
        self.enrollment.save()
        Team.objects.all().delete()
        self.program.features.clear()

        self.client.login(username="mentor_user", password="password123")  # nosec B106
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)

        self.assertContains(resp, "Program Status")
        self.assertContains(resp, "Active")

    def test_lead_mentor_sees_clearance_card_and_dynamic_card(self):
        # Lead mentor should see Clearances / Safety card
        self.client.login(username="lead_user", password="password123")  # nosec B106
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)

        self.assertContains(resp, "Clearances / Safety")
        # And also the dynamic card (Roster Assignments)
        self.assertContains(resp, "Roster Assignments")

    def test_mentor_does_not_see_clearance_card(self):
        self.client.login(username="mentor_user", password="password123")  # nosec B106
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)

        self.assertNotContains(resp, "Clearances / Safety")

    def test_active_students_card_displays_group_counts_and_breakdown(self):
        # Create subteam and crew (project)
        subteam = SubTeam.objects.create(
            name="Mechanical", program=self.program, color="#2196f3"
        )
        crew = Crew.objects.create(
            name="Chassis Project", program=self.program, color="#4caf50"
        )

        # Assign subteam and crew to enrollment
        self.enrollment.subteam = subteam
        self.enrollment.crew = crew
        self.enrollment.save()

        # Add second student in FTC team
        student2 = Student.objects.create(
            legal_first_name="Alice",
            last_name="Smith",
            date_of_birth=datetime.date(2009, 3, 15),
        )
        team2 = Team.objects.create(
            team_type="FTC",
            number=9820,
            name="Girls of Steel FTC",
            color="#9c27b0",
        )
        Enrollment.objects.create(
            student=student2,
            program=self.program,
            active=True,
            team=team2,
            subteam=subteam,
        )

        self.client.login(username="mentor_user", password="password123")  # nosec B106
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)

        # Active students title and main count
        self.assertContains(resp, "Active Students")
        self.assertContains(resp, "2 students")

        # Subtitle with teams · subteams · projects formatted with dot
        self.assertContains(resp, "2 teams &middot; 1 subteam &middot; 1 project")

        # Group breakdown pills
        self.assertContains(resp, "FRC 3504: 1")
        self.assertContains(resp, "FTC 9820: 1")
        self.assertContains(resp, "Mechanical: 2")
        self.assertContains(resp, "Chassis Project: 1")

    def test_active_students_card_with_zero_groups_assigned(self):
        self.enrollment.team = None
        self.enrollment.save()

        self.client.login(username="mentor_user", password="password123")  # nosec B106
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)

        self.assertContains(resp, "1 student")
        self.assertContains(resp, "0 teams &middot; 0 subteams &middot; 0 projects")
