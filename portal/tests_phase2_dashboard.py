import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from applications.models import Application
from orders.models import OrderItem, Vendor
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


class Phase2DashboardViewTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Test High School")
        self.program = Program.objects.create(
            name="Girls of Steel FRC 2026-2027",
            active=True,
            start_date=datetime.date(2026, 9, 1),
            end_date=datetime.date(2027, 5, 31),
        )
        self.lead_group, _ = Group.objects.get_or_create(name="LeadMentor")

    def test_lead_mentor_dashboard_overview_and_quick_actions(self):
        lead_user = User.objects.create_user(
            username="lead_user", password="password123"  # nosec B106
        )
        lead_user.groups.add(self.lead_group)

        # Create student and application
        student = Student.objects.create(
            legal_first_name="Jane",
            last_name="Doe",
            school=self.school,
            date_of_birth=datetime.date(2009, 5, 1),
        )
        Enrollment.objects.create(student=student, program=self.program, active=True)
        Application.objects.create(
            program=self.program,
            status=Application.Status.SUBMITTED,
            data={"step5-student": {"legal_first_name": "Applicant"}},
        )
        vendor = Vendor.objects.create(name="VEX Robotics")
        OrderItem.objects.create(
            item_name="Motor Controller",
            quantity=2,
            vendor=vendor,
            requested_by=lead_user,
            program=self.program,
        )

        self.client.login(username="lead_user", password="password123")  # nosec B106
        resp = self.client.get(reverse("profile_dashboard"))
        self.assertEqual(resp.status_code, 200)

        # Verify Lead Mentor Overview section
        self.assertContains(resp, "Lead Mentor Overview")
        self.assertContains(resp, "Active Programs")
        self.assertContains(resp, "Active Students")
        self.assertContains(resp, "Review Applications")
        self.assertContains(resp, "Order Requests")
        self.assertContains(resp, "Quick Actions:")
        self.assertContains(resp, reverse("application_review_list"))
        self.assertContains(resp, reverse("all_attendance"))
        self.assertContains(resp, reverse("portal_settings"))

    def test_multi_role_user_dashboard_role_filter_tabs(self):
        multi_user = User.objects.create_user(
            username="multi_user", password="password123"  # nosec B106
        )
        multi_user.groups.add(self.lead_group)

        # Adult profile as Parent and Mentor
        adult = Adult.objects.create(
            user=multi_user,
            legal_first_name="Alex",
            last_name="MentorParent",
            is_parent=True,
            is_mentor=True,
            phone_number="412-555-1234",
        )
        student = Student.objects.create(
            legal_first_name="Sam",
            last_name="MentorParent",
            school=self.school,
            date_of_birth=datetime.date(2010, 1, 1),
        )
        AdultStudentRelationship.objects.create(
            adult=adult, student=student, relationship_to_student="mother"
        )
        Enrollment.objects.create(student=student, program=self.program, active=True)

        self.client.login(username="multi_user", password="password123")  # nosec B106
        resp = self.client.get(reverse("profile_dashboard"))
        self.assertEqual(resp.status_code, 200)

        # Multi-role filter bar is present
        self.assertContains(resp, 'id="dashboardRoleFilter"')
        self.assertContains(resp, "All Views")
        self.assertContains(resp, 'data-role-filter="lead-mentor"')
        self.assertContains(resp, 'data-role-filter="mentor"')
        self.assertContains(resp, 'data-role-filter="parent"')

        # Check section classes for JS role filtering
        self.assertContains(resp, "role-lead-mentor")
        self.assertContains(resp, "role-mentor")
        self.assertContains(resp, "role-parent")

        # CSP Compliance: verify script has nonce attribute
        self.assertRegex(resp.content.decode("utf-8"), r'<script nonce="[^"]+">')

    def test_student_dashboard_team_badges_and_quick_links(self):
        student_user = User.objects.create_user(
            username="student_features", password="password123"  # nosec B106
        )
        student = Student.objects.create(
            user=student_user,
            legal_first_name="Zoe",
            last_name="Robotics",
            school=self.school,
            graduation_year=2027,
        )
        team = Team.objects.create(
            team_type="FRC", number=3504, name="Girls of Steel", color="#198754"
        )
        subteam = SubTeam.objects.create(
            program=self.program, name="Mechanical", color="#0d6efd"
        )
        crew = Crew.objects.create(
            program=self.program, name="Alpha Crew", color="#6c757d"
        )

        # Enable orders and outreach features
        feat_orders, _ = ProgramFeature.objects.get_or_create(
            key="orders", defaults={"name": "Orders"}
        )
        self.program.features.add(feat_orders)

        Enrollment.objects.create(
            student=student,
            program=self.program,
            team=team,
            subteam=subteam,
            crew=crew,
            active=True,
        )

        self.client.login(
            username="student_features", password="password123"
        )  # nosec B106
        resp = self.client.get(reverse("profile_dashboard"))
        self.assertEqual(resp.status_code, 200)

        # Student program card contains team, crew, subteam badges
        self.assertContains(resp, "FRC 3504")
        self.assertContains(resp, "Mechanical")
        self.assertContains(resp, "Alpha Crew")

        # Quick action links
        self.assertContains(resp, reverse("orders:order_list", args=[self.program.pk]))
        self.assertContains(
            resp, reverse("program_student_map", args=[self.program.pk])
        )
        self.assertContains(resp, reverse("program_detail", args=[self.program.pk]))

    def test_parent_dashboard_family_total_due_banner(self):
        parent_user = User.objects.create_user(
            username="parent_due", password="password123"  # nosec B106
        )
        adult = Adult.objects.create(
            user=parent_user,
            legal_first_name="Pat",
            last_name="Parent",
            is_parent=True,
            phone_number="412-555-4321",
        )
        student = Student.objects.create(
            legal_first_name="Chris",
            last_name="Parent",
            school=self.school,
            date_of_birth=datetime.date(2009, 8, 12),
        )
        AdultStudentRelationship.objects.create(
            adult=adult, student=student, relationship_to_student="father"
        )
        Enrollment.objects.create(student=student, program=self.program, active=True)

        # Create fee so balance is owed
        Fee.objects.create(
            program=self.program,
            name="Season Registration Fee",
            amount=Decimal("150.00"),
        )

        self.client.login(username="parent_due", password="password123")  # nosec B106
        resp = self.client.get(reverse("profile_dashboard"))
        self.assertEqual(resp.status_code, 200)

        # Family Total Due banner with Pay Dues CTA
        self.assertContains(resp, "Total Due:")
        self.assertContains(resp, "$150.00")
        self.assertContains(resp, "Pay Dues")
        self.assertContains(resp, reverse("parent_payments"))

    def test_alumni_dashboard_rendering(self):
        alumni_user = User.objects.create_user(
            username="alumni_user", password="password123"  # nosec B106
        )
        student = Student.objects.create(
            legal_first_name="Former",
            last_name="Student",
            school=self.school,
            graduation_year=2024,
            graduated=True,
        )
        adult = Adult.objects.create(
            user=alumni_user,
            legal_first_name="Former",
            last_name="Student",
            is_alumni=True,
            student_record=student,
            college="Carnegie Mellon University",
            field_of_study="Computer Science",
            employer="Robotics Institute",
        )
        Enrollment.objects.create(student=student, program=self.program, active=False)

        self.client.login(username="alumni_user", password="password123")  # nosec B106
        resp = self.client.get(reverse("profile_dashboard"))
        self.assertEqual(resp.status_code, 200)

        self.assertContains(resp, "Alumni Profile")
        self.assertContains(resp, "Carnegie Mellon University")
        self.assertContains(resp, "Computer Science")
        self.assertContains(resp, "Robotics Institute")
        self.assertContains(resp, "My Program History")
        self.assertContains(resp, self.program.name)

    def test_student_dashboard_role_badge(self):
        student_user = User.objects.create_user(
            username="student_dash", password="password123"  # nosec B106
        )
        student = Student.objects.create(
            user=student_user,
            legal_first_name="Maya",
            last_name="Lin",
            school=self.school,
            graduation_year=2028,
        )
        Enrollment.objects.create(student=student, program=self.program, active=True)

        self.client.login(username="student_dash", password="password123")  # nosec B106
        resp = self.client.get(reverse("profile_dashboard"))
        self.assertEqual(resp.status_code, 200)

        # Welcome header has Student badge
        self.assertContains(resp, "Welcome, Maya!")
        self.assertContains(resp, "Student")
        self.assertContains(resp, "My Programs")
