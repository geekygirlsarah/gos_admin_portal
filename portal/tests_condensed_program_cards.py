import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from programs.models import (
    Adult,
    AdultStudentRelationship,
    Crew,
    Enrollment,
    Fee,
    Program,
    ProgramFeature,
    School,
    Student,
    SubTeam,
    Team,
)


class CondensedProgramCardsTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Central High")
        self.lead_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.mentor_group, _ = Group.objects.get_or_create(name="Mentor")

        self.program = Program.objects.create(
            name="Girls of Steel FRC 2026-2027",
            active=True,
            description="A very long program description that should not clutter the condensed dashboard program card view.",
            start_date=datetime.date(2026, 9, 1),
            end_date=datetime.date(2027, 5, 31),
        )
        self.upcoming_program = Program.objects.create(
            name="Girls of Steel FTC 2026-2027",
            active=True,
            description="Another detailed description for the upcoming FTC robotics competition program.",
            start_date=datetime.date(2026, 11, 1),
            end_date=datetime.date(2027, 4, 30),
        )

        self.feat_att, _ = ProgramFeature.objects.get_or_create(
            key="attendance", defaults={"name": "Attendance"}
        )
        self.feat_outreach, _ = ProgramFeature.objects.get_or_create(
            key="outreach", defaults={"name": "Outreach"}
        )
        self.feat_orders, _ = ProgramFeature.objects.get_or_create(
            key="orders", defaults={"name": "Orders"}
        )

        self.program.features.add(self.feat_att, self.feat_outreach, self.feat_orders)

    def test_mentor_dashboard_program_cards_no_descriptions(self):
        mentor_user = User.objects.create_user(
            username="mentor_condensed", password="password123"  # nosec B106
        )
        mentor_user.groups.add(self.mentor_group)
        Adult.objects.create(
            user=mentor_user,
            legal_first_name="Mentor",
            last_name="Test",
            is_mentor=True,
            mentor_active=True,
        )

        self.client.login(
            username="mentor_condensed", password="password123"
        )  # nosec B106
        resp = self.client.get(reverse("profile_dashboard"))
        self.assertEqual(resp.status_code, 200)

        # Program titles and dates should be present
        self.assertContains(resp, "Girls of Steel FRC 2026-2027")
        self.assertContains(resp, "Girls of Steel FTC 2026-2027")
        self.assertContains(resp, "2026")
        self.assertContains(resp, "2027")

        # Long descriptions must NOT be in the cards
        self.assertNotContains(resp, "A very long program description")
        self.assertNotContains(resp, "Another detailed description")

        # View details action links
        self.assertContains(resp, reverse("program_detail", args=[self.program.pk]))
        self.assertContains(
            resp, reverse("program_detail", args=[self.upcoming_program.pk])
        )

    def test_student_dashboard_program_card_condensed_and_modern(self):
        student_user = User.objects.create_user(
            username="student_condensed", password="password123"  # nosec B106
        )
        student = Student.objects.create(
            user=student_user,
            legal_first_name="Sasha",
            last_name="Student",
            school=self.school,
            graduation_year=2028,
        )
        team = Team.objects.create(
            team_type="FRC", number=3504, name="Girls of Steel", color="#0d6efd"
        )
        subteam = SubTeam.objects.create(
            program=self.program, name="Software", color="#198754"
        )
        crew = Crew.objects.create(
            program=self.program, name="Beta Crew", color="#6c757d"
        )

        Enrollment.objects.create(
            student=student,
            program=self.program,
            team=team,
            subteam=subteam,
            crew=crew,
            active=True,
        )

        self.client.login(
            username="student_condensed", password="password123"
        )  # nosec B106
        resp = self.client.get(reverse("profile_dashboard"))
        self.assertEqual(resp.status_code, 200)

        # Program title and group badges
        self.assertContains(resp, "Girls of Steel FRC 2026-2027")
        self.assertContains(resp, "FRC 3504")
        self.assertContains(resp, "Software")
        self.assertContains(resp, "Beta Crew")

        # Dates & CTAs
        self.assertContains(resp, "Dates:")
        self.assertContains(resp, reverse("orders:order_list", args=[self.program.pk]))
        self.assertContains(
            resp, reverse("program_student_map", args=[self.program.pk])
        )
        self.assertContains(resp, reverse("program_detail", args=[self.program.pk]))

    def test_parent_dashboard_program_card_condensed(self):
        parent_user = User.objects.create_user(
            username="parent_condensed", password="password123"  # nosec B106
        )
        adult = Adult.objects.create(
            user=parent_user,
            legal_first_name="Morgan",
            last_name="Parent",
            is_parent=True,
        )
        student = Student.objects.create(
            legal_first_name="Taylor",
            last_name="Parent",
            school=self.school,
            graduation_year=2029,
        )
        AdultStudentRelationship.objects.create(
            adult=adult, student=student, relationship_to_student="guardian"
        )
        Enrollment.objects.create(student=student, program=self.program, active=True)

        Fee.objects.create(
            program=self.program,
            name="Registration",
            amount=Decimal("100.00"),
        )

        self.client.login(
            username="parent_condensed", password="password123"
        )  # nosec B106
        resp = self.client.get(reverse("profile_dashboard"))
        self.assertEqual(resp.status_code, 200)

        # Student card & program row
        self.assertContains(resp, "Taylor Parent")
        self.assertContains(resp, "Girls of Steel FRC 2026-2027")
        self.assertContains(resp, "Balance Owed:")
        self.assertContains(resp, "$100.00")
        self.assertContains(
            resp, reverse("program_student_map", args=[self.program.pk])
        )
