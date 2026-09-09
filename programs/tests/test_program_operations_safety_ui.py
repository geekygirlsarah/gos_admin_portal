from cryptography.fernet import Fernet
from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from programs.models import (
    Adult,
    Crew,
    Enrollment,
    Program,
    ProgramDocument,
    School,
    Student,
    StudentDocument,
    SubTeam,
    Team,
)

TEST_FILE_KEY = Fernet.generate_key().decode()


@override_settings(FILE_ENCRYPTION_KEY=TEST_FILE_KEY)
class ProgramOperationsSafetyUITests(TestCase):
    def setUp(self):
        self.lead_mentor_user = User.objects.create_user(
            username="lead_mentor", password="password123"
        )  # nosec B106
        lm_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.lead_mentor_user.groups.add(lm_group)

        self.mentor_user = User.objects.create_user(
            username="mentor_user", password="password123"
        )  # nosec B106
        self.mentor_adult = Adult.objects.create(
            user=self.mentor_user,
            legal_first_name="Jane",
            last_name="Mentor",
            is_mentor=True,
            mentor_active=True,
        )

        self.program = Program.objects.create(
            name="Robotics 2026-2027",
            active=True,
        )

        self.school = School.objects.create(name="Central High School")

        # Create students
        self.student1 = Student.objects.create(
            legal_first_name="Alice",
            last_name="Smith",
            school=self.school,
            allergies="Peanuts, Shellfish",
            dietary_restrictions="Vegetarian",
            medical_notes="Asthma inhaler in backpack",
            graduation_year=2027,
            phone_number="412-555-0101",
            personal_email="alice@example.com",
        )
        self.student2 = Student.objects.create(
            legal_first_name="Bob",
            last_name="Jones",
            school=self.school,
            dietary_restrictions="Gluten-Free",
            graduation_year=2028,
            phone_number="412-555-0102",
            personal_email="bob@example.com",
        )
        self.student3 = Student.objects.create(
            legal_first_name="Charlie",
            last_name="Brown",
            graduation_year=2026,
            phone_number="412-555-0103",
            personal_email="charlie@example.com",
        )

        # Enrollments
        self.e1 = Enrollment.objects.create(
            student=self.student1, program=self.program, active=True
        )
        self.e2 = Enrollment.objects.create(
            student=self.student2, program=self.program, active=True
        )
        self.e3 = Enrollment.objects.create(
            student=self.student3, program=self.program, active=True
        )

        # Teams / Crews / Subteams
        self.team = Team.objects.create(
            team_type="FRC", number=3504, name="Girls of Steel"
        )
        self.crew = Crew.objects.create(name="Chassis Build", program=self.program)
        self.subteam = SubTeam.objects.create(name="Mechanical", program=self.program)

        self.e1.team = self.team
        self.e1.crew = self.crew
        self.e1.subteam = self.subteam
        self.e1.save()

        # Documents
        self.doc = ProgramDocument.objects.create(
            program=self.program,
            name="Safety Agreement",
            is_required=True,
            display_order=1,
        )
        StudentDocument.objects.create(
            student=self.student1,
            program_document=self.doc,
            file=SimpleUploadedFile("safety.pdf", b"dummy content"),
        )

    def _login(self, user):
        self.client.login(username=user.username, password="password123")  # nosec B106

    def test_medical_info_page_ui_and_kpis(self):
        self._login(self.mentor_user)
        url = reverse("program_medical_info", args=[self.program.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        # Check KPI context values
        self.assertEqual(response.context["total_with_info"], 2)
        self.assertEqual(response.context["allergies_count"], 1)
        self.assertEqual(response.context["dietary_count"], 2)
        self.assertEqual(response.context["medical_notes_count"], 1)

        # Check rendered content
        self.assertContains(response, "Medical &amp; Allergy Info")
        self.assertContains(response, "Allergy Alerts")
        self.assertContains(response, "Dietary Restrictions")
        self.assertContains(response, "Medical Notes")
        self.assertContains(response, 'id="printRosterBtn"')
        self.assertContains(response, "Print Roster")
        self.assertContains(response, "window.print()")
        self.assertContains(response, "Peanuts, Shellfish")
        self.assertContains(response, "Vegetarian")
        self.assertContains(response, "Asthma inhaler in backpack")
        self.assertNotContains(response, 'onclick="window.print()"')
        # Check CSP nonce presence
        self.assertIn("csp_nonce", response.context)
        self.assertContains(response, f'nonce="{response.context["csp_nonce"]}"')

    def test_assignment_page_ui_and_kpis(self):
        self._login(self.lead_mentor_user)
        url = reverse("program_assignment", args=[self.program.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        # Check KPI context values
        self.assertEqual(response.context["active_students_count"], 3)
        self.assertEqual(response.context["assigned_team_count"], 1)
        self.assertEqual(response.context["unassigned_team_count"], 2)
        self.assertEqual(response.context["assigned_crew_count"], 1)
        self.assertEqual(response.context["unassigned_crew_count"], 2)
        self.assertEqual(response.context["assigned_subteam_count"], 1)
        self.assertEqual(response.context["unassigned_subteam_count"], 2)

        # Check rendered UI elements
        self.assertContains(response, "Team/Crew Assignment")
        self.assertContains(response, "Team Assignments")
        self.assertContains(response, "Crew / Project")
        self.assertContains(response, "Subteam Assignments")
        self.assertContains(response, "Filter students by name...")
        self.assertContains(response, "Needs Team")
        self.assertContains(response, "Needs Crew")
        self.assertContains(response, "Needs Subteam")
        self.assertContains(response, "js-confirm")
        self.assertNotContains(response, "onsubmit=")

    def test_emergency_contacts_page_ui(self):
        self._login(self.mentor_user)
        url = reverse("program_emergency_contacts", args=[self.program.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        self.assertContains(response, "Emergency Contacts")
        self.assertContains(response, 'id="printRosterBtn"')
        self.assertContains(response, "Print Roster")
        self.assertContains(response, "window.print()")
        self.assertContains(response, "Alice Smith")
        self.assertContains(response, "412-555-0101")
        self.assertContains(response, "alice@example.com")
        self.assertNotContains(response, 'onclick="window.print()"')
        # Stats are removed to maximize operational speed on field/mobile
        self.assertNotContains(response, "Primary Guardians")
        self.assertNotContains(response, "Secondary Guardians")
        # Check CSP nonce presence
        self.assertIn("csp_nonce", response.context)
        self.assertContains(response, f'nonce="{response.context["csp_nonce"]}"')

    def test_student_documents_page_ui(self):
        self._login(self.lead_mentor_user)
        url = reverse("program_student_documents", args=[self.program.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        self.assertContains(response, "Student Documents")
        self.assertContains(response, 'id="printMatrixBtn"')
        self.assertContains(response, "Safety Agreement")
        self.assertContains(response, "Alice Smith")
        self.assertContains(response, "Bob Jones")
        self.assertContains(response, "window.print()")
        self.assertNotContains(response, 'onclick="window.print()"')
        self.assertIn("csp_nonce", response.context)
        self.assertContains(response, f'nonce="{response.context["csp_nonce"]}"')

    def test_schools_breakdown_page_ui(self):
        self._login(self.lead_mentor_user)
        url = reverse("program_schools", args=[self.program.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        self.assertContains(response, "Central High School")
        self.assertContains(response, "No School")
        self.assertContains(response, "Alice Smith")
        self.assertContains(response, "Bob Jones")
        self.assertContains(response, "Charlie Brown")
        self.assertContains(response, 'id="printBreakdownBtn"')
        self.assertContains(response, "window.print()")
        self.assertNotContains(response, 'onclick="window.print()"')
        self.assertIn("csp_nonce", response.context)
        self.assertContains(response, f'nonce="{response.context["csp_nonce"]}"')
