import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from programs.models import (
    Adult,
    AdultStudentRelationship,
    BackgroundCheck,
    Enrollment,
    Fee,
    Payment,
    Program,
    ProgramFeature,
    SlidingScale,
    Student,
    Team,
)


class Phase1UIImprovementsTests(TestCase):
    def setUp(self):
        password = "password123"  # nosec B105
        # Lead Mentor
        self.lead_user = User.objects.create_user(
            username="lead_user", password=password
        )
        lm_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.lead_user.groups.add(lm_group)

        # Mentor
        self.mentor_user = User.objects.create_user(
            username="mentor_user", password=password
        )
        self.mentor_adult = Adult.objects.create(
            user=self.mentor_user,
            legal_first_name="Mentor",
            last_name="Person",
            is_mentor=True,
        )

        # Parent and Student
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
            preferred_first_name="Janie",
            last_name="Doe",
            date_of_birth=datetime.date(2008, 5, 10),
        )
        AdultStudentRelationship.objects.create(
            adult=self.parent_adult,
            student=self.student,
            relationship_to_student="parent",
        )

        # Program
        self.program = Program.objects.create(
            name="Robotics Flagship",
            active=True,
            start_date=datetime.date(2026, 1, 1),
            end_date=datetime.date(2026, 12, 31),
        )
        f_att, _ = ProgramFeature.objects.get_or_create(
            key="attendance", defaults={"name": "Attendance"}
        )
        f_bg, _ = ProgramFeature.objects.get_or_create(
            key="background-checks", defaults={"name": "Background Checks"}
        )
        f_so, _ = ProgramFeature.objects.get_or_create(
            key="signout-sheet", defaults={"name": "Sign-out Sheet"}
        )
        self.program.features.add(f_att, f_bg, f_so)

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

    def test_program_detail_renders_kpi_cards_and_action_dropdowns(self):
        self.client.login(username="lead_user", password="password123")  # nosec B106
        url = reverse("program_detail", args=[self.program.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        # Verify dropdowns exist in unified toolbar
        self.assertContains(response, "Manage Roster")
        self.assertContains(response, "Views")
        self.assertContains(response, "Finances")

        # Verify summary KPI metrics are passed and displayed
        self.assertIn("active_enrollments_count", response.context)
        self.assertEqual(response.context["active_enrollments_count"], 1)
        self.assertContains(response, "Active Students")

        # Student display name and team pill
        self.assertContains(response, "Janie Doe")
        self.assertContains(response, "FRC 3504")

    def test_program_detail_shows_no_fees_alert_for_lead_mentor(self):
        self.client.login(username="lead_user", password="password123")  # nosec B106
        url = reverse("program_detail", args=[self.program.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "alert alert-warning")
        self.assertContains(response, "No fees have been added to this program yet")

    def test_program_detail_shows_bg_check_kpi_and_badge_when_needed(self):
        self.client.login(username="lead_user", password="password123")  # nosec B106
        url = reverse("program_detail", args=[self.program.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        # Student Jane is 18 in 2026, so requires BG check and doesn't have one
        self.assertContains(response, "BG Check Needed")

        # Now give student all 3 cleared BG checks
        for c_type in ["state_police", "child_abuse", "fbi"]:
            BackgroundCheck.objects.create(
                student=self.student,
                check_type=c_type,
                cleared=True,
                obtained_date=datetime.date(2026, 1, 1),
            )
        response2 = self.client.get(url)
        self.assertEqual(response2.status_code, 200)
        self.assertNotContains(response2, "BG Check Needed")

    def test_parent_payments_modern_summary_and_student_cards(self):
        # Create a fee and payment
        Fee.objects.create(
            program=self.program,
            name="Registration Fee",
            amount=Decimal("150.00"),
            effective_date=datetime.date.today(),
        )
        Payment.objects.create(
            student=self.student,
            program=self.program,
            amount=Decimal("50.00"),
            paid_on=datetime.date.today(),
        )

        self.client.login(username="parent_user", password="password123")  # nosec B106
        url = reverse("parent_payments")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        # Check total owed and badge
        self.assertEqual(response.context["grand_total"], Decimal("100.00"))
        self.assertContains(response, "Total Due: $100.00")
        self.assertContains(response, "Janie Doe")
        self.assertContains(response, "Robotics Flagship")
        self.assertContains(response, "View Balance")
        self.assertContains(response, "Apply for Sliding Scale")

    def test_parent_payments_paid_in_full_state(self):
        # Program with 0 balance
        self.client.login(username="parent_user", password="password123")  # nosec B106
        url = reverse("parent_payments")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        self.assertEqual(response.context["grand_total"], Decimal("0.00"))
        self.assertContains(response, "Paid in Full")

    def test_parent_payments_sliding_scale_badge_and_withdraw(self):
        application = SlidingScale.objects.create(
            student=self.student,
            family_size=4,
            adjusted_gross_income=Decimal("30000.00"),
            status=SlidingScale.STATUS_PENDING,
            applied_by=self.parent_adult,
        )

        self.client.login(username="parent_user", password="password123")  # nosec B106
        url = reverse("parent_payments")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pending Review")
        self.assertContains(response, "Withdraw Application")

    def test_program_student_balance_sheet(self):
        Fee.objects.create(
            program=self.program,
            name="Season Dues",
            amount=Decimal("200.00"),
            effective_date=datetime.date.today(),
        )
        Payment.objects.create(
            student=self.student,
            program=self.program,
            amount=Decimal("200.00"),
            paid_on=datetime.date.today(),
        )

        self.client.login(username="parent_user", password="password123")  # nosec B106
        url = reverse(
            "program_student_balance", args=[self.program.pk, self.student.pk]
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        self.assertContains(response, "Balance Sheet")
        self.assertContains(response, "Season Dues")
        self.assertContains(response, "Printable Version")
