import datetime

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from organizations.models import Organization
from programs.forms import BackgroundChecksForm, BackgroundCheckTypeForm
from programs.models import (
    Adult,
    BackgroundCheck,
    BackgroundCheckType,
    Enrollment,
    Program,
    Student,
)


class BackgroundCheckTypeModelTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Test Org", slug="test-org")

    def test_create_and_str(self):
        bct = BackgroundCheckType.objects.create(
            organization=self.org,
            name="State Criminal History",
            validity_years=5,
            applies_to_students=True,
            student_min_age=17,
        )
        self.assertEqual(str(bct), "State Criminal History")
        self.assertTrue(bct.is_active)
        self.assertEqual(bct.validity_years, 5)

    def test_applies_to_student_age_by_date_criteria(self):
        bct = BackgroundCheckType.objects.create(
            organization=self.org,
            name="State Police",
            validity_years=5,
            applies_to_students=True,
            student_min_age_by_date=17,
            student_min_age_cutoff_date=datetime.date(2000, 9, 1),
        )
        # Academic year anchor: Sept 1
        # Student born in 2008 is >=17 on Sept 1 2026
        s_old = Student.objects.create(
            legal_first_name="Old",
            last_name="Student",
            date_of_birth=datetime.date(2008, 1, 1),
            organization=self.org,
        )
        s_young = Student.objects.create(
            legal_first_name="Young",
            last_name="Student",
            date_of_birth=datetime.date(2012, 1, 1),
            organization=self.org,
        )
        s_no_dob = Student(
            legal_first_name="No",
            last_name="DOB",
            date_of_birth=None,
            organization=self.org,
        )

        with timezone.override(datetime.timezone.utc):
            self.assertTrue(bct.applies_to_student(s_old))
            self.assertFalse(bct.applies_to_student(s_young))
            self.assertFalse(bct.applies_to_student(s_no_dob))

    def test_applies_to_student_during_program_enrollment_criteria(self):
        bct = BackgroundCheckType.objects.create(
            organization=self.org,
            name="Adult Law Clearance",
            validity_years=5,
            applies_to_students=True,
            student_min_age_during_program=18,
        )
        # Program running from Sept 1 2026 to May 31 2027
        program = Program.objects.create(
            name="Robotics Season 2026-2027",
            start_date=datetime.date(2026, 9, 1),
            end_date=datetime.date(2027, 5, 31),
            active=True,
            organization=self.org,
        )
        # Student turns 18 on March 15, 2027 (during the program)
        s_turns_18 = Student.objects.create(
            legal_first_name="Turning18",
            last_name="Student",
            date_of_birth=datetime.date(2009, 3, 15),
            organization=self.org,
        )
        Enrollment.objects.create(
            student=s_turns_18, program=program, active=True, organization=self.org
        )

        # Student turns 18 on October 15, 2027 (after the program ends)
        s_stays_17 = Student.objects.create(
            legal_first_name="Stays17",
            last_name="Student",
            date_of_birth=datetime.date(2009, 10, 15),
            organization=self.org,
        )
        Enrollment.objects.create(
            student=s_stays_17, program=program, active=True, organization=self.org
        )

        # Student who turned 18 before the program
        s_already_18 = Student.objects.create(
            legal_first_name="Already18",
            last_name="Student",
            date_of_birth=datetime.date(2008, 1, 1),
            organization=self.org,
        )
        Enrollment.objects.create(
            student=s_already_18, program=program, active=True, organization=self.org
        )

        # Student turns 18 during program, but enrollment is inactive
        s_inactive = Student.objects.create(
            legal_first_name="Inactive",
            last_name="Student",
            date_of_birth=datetime.date(2009, 3, 15),
            organization=self.org,
        )
        Enrollment.objects.create(
            student=s_inactive, program=program, active=False, organization=self.org
        )

        with timezone.override(datetime.timezone.utc):
            self.assertTrue(bct.applies_to_student(s_turns_18))
            self.assertFalse(bct.applies_to_student(s_stays_17))
            self.assertTrue(bct.applies_to_student(s_already_18))
            self.assertFalse(bct.applies_to_student(s_inactive))

    def test_applies_to_student_both_criteria_or_logic(self):
        # Requires clearance if turning 17 by Sept 1 OR turning 18 during program
        bct = BackgroundCheckType.objects.create(
            organization=self.org,
            name="University and State Law Combined",
            validity_years=5,
            applies_to_students=True,
            student_min_age_by_date=17,
            student_min_age_cutoff_date=datetime.date(2000, 9, 1),
            student_min_age_during_program=18,
        )
        program = Program.objects.create(
            name="Robotics Season 2026-2027",
            start_date=datetime.date(2026, 9, 1),
            end_date=datetime.date(2027, 5, 31),
            active=True,
            organization=self.org,
        )

        # Student born 2009-08-15: Was 17 by Sept 1, 2026 (matches criterion 1)
        s1 = Student.objects.create(
            legal_first_name="Student1",
            last_name="Test",
            date_of_birth=datetime.date(2009, 8, 15),
            organization=self.org,
        )
        Enrollment.objects.create(
            student=s1, program=program, active=True, organization=self.org
        )

        # Student born 2009-11-15: Not 17 by Sept 1, 2026, but turns 17 during program (not 18) -> neither
        s2 = Student.objects.create(
            legal_first_name="Student2",
            last_name="Test",
            date_of_birth=datetime.date(2009, 11, 15),
            organization=self.org,
        )
        Enrollment.objects.create(
            student=s2, program=program, active=True, organization=self.org
        )

        with timezone.override(datetime.timezone.utc):
            self.assertTrue(bct.applies_to_student(s1))
            self.assertFalse(bct.applies_to_student(s2))

    def test_applies_to_student_all_ages(self):
        bct = BackgroundCheckType.objects.create(
            organization=self.org,
            name="General Waiver",
            validity_years=1,
            applies_to_students=True,
            student_min_age=None,
        )
        s_young = Student.objects.create(
            legal_first_name="Young",
            last_name="Student",
            date_of_birth=datetime.date(2012, 1, 1),
            organization=self.org,
        )
        self.assertTrue(bct.applies_to_student(s_young))

        # When applies_to_students is False
        bct.applies_to_students = False
        bct.save()
        self.assertFalse(bct.applies_to_student(s_young))

    def test_applies_to_adult_roles(self):
        bct_mentor = BackgroundCheckType.objects.create(
            organization=self.org,
            name="Mentor Check",
            validity_years=2,
            applies_to_mentors=True,
            applies_to_lead_mentors=True,
            applies_to_parents=False,
            applies_to_alumni=False,
        )
        bct_parent = BackgroundCheckType.objects.create(
            organization=self.org,
            name="Parent Check",
            validity_years=1,
            applies_to_mentors=False,
            applies_to_lead_mentors=False,
            applies_to_parents=True,
            applies_to_alumni=False,
        )

        mentor = Adult.objects.create(
            legal_first_name="Jane",
            last_name="Mentor",
            is_mentor=True,
            organization=self.org,
        )
        parent = Adult.objects.create(
            legal_first_name="John",
            last_name="Parent",
            is_parent=True,
            organization=self.org,
        )
        alumni = Adult.objects.create(
            legal_first_name="Alice",
            last_name="Alumni",
            is_alumni=True,
            organization=self.org,
        )

        self.assertTrue(bct_mentor.applies_to_adult(mentor))
        self.assertFalse(bct_mentor.applies_to_adult(parent))
        self.assertFalse(bct_mentor.applies_to_adult(alumni))

        self.assertFalse(bct_parent.applies_to_adult(mentor))
        self.assertTrue(bct_parent.applies_to_adult(parent))
        self.assertFalse(bct_parent.applies_to_adult(alumni))


class DynamicBackgroundCheckRequirementTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Test Org", slug="test-org")
        self.bct1 = BackgroundCheckType.objects.create(
            organization=self.org,
            name="Check 1",
            validity_years=5,
            applies_to_students=True,
            applies_to_mentors=True,
            student_min_age=17,
        )
        self.bct2 = BackgroundCheckType.objects.create(
            organization=self.org,
            name="Check 2",
            validity_years=3,
            applies_to_students=True,
            applies_to_mentors=True,
            student_min_age=17,
        )
        self.student = Student.objects.create(
            legal_first_name="Sam",
            last_name="Student",
            date_of_birth=datetime.date(2008, 1, 1),
            organization=self.org,
        )
        self.mentor = Adult.objects.create(
            legal_first_name="Mary",
            last_name="Mentor",
            is_mentor=True,
            organization=self.org,
        )

    def test_student_and_adult_needs_background_check(self):
        self.assertTrue(self.student.requires_background_check())
        self.assertTrue(self.student.needs_background_check())
        self.assertTrue(self.mentor.requires_background_check())
        self.assertTrue(self.mentor.needs_background_check())

        # Clear check 1 for student
        BackgroundCheck.objects.create(
            student=self.student,
            check_type=self.bct1,
            cleared=True,
            obtained_date=timezone.localdate(),
            organization=self.org,
        )
        self.assertTrue(self.student.needs_background_check())

        # Clear check 2 for student
        bc2 = BackgroundCheck.objects.create(
            student=self.student,
            check_type=self.bct2,
            cleared=True,
            obtained_date=timezone.localdate(),
            organization=self.org,
        )
        self.assertFalse(self.student.needs_background_check())

        # Expire check 2 (validity 3 years)
        bc2.obtained_date = timezone.localdate() - datetime.timedelta(days=365 * 4)
        bc2.save()
        self.assertTrue(self.student.needs_background_check())

    def test_custom_validity_years_expiration(self):
        bct_1yr = BackgroundCheckType.objects.create(
            organization=self.org,
            name="Annual Clearance",
            validity_years=1,
            applies_to_mentors=True,
        )
        check = BackgroundCheck.objects.create(
            adult=self.mentor,
            check_type=bct_1yr,
            cleared=True,
            obtained_date=datetime.date(2025, 5, 10),
            organization=self.org,
        )
        self.assertEqual(check.expiration_date, datetime.date(2026, 5, 10))


class BackgroundChecksFormDynamicTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Test Org", slug="test-org")
        self.bct1 = BackgroundCheckType.objects.create(
            organization=self.org,
            name="State Police",
            validity_years=5,
        )
        self.bct2 = BackgroundCheckType.objects.create(
            organization=self.org,
            name="FBI Fingerprint",
            validity_years=5,
        )
        self.student = Student.objects.create(
            legal_first_name="A", last_name="B", organization=self.org
        )

    def test_form_fields_generated_from_active_types(self):
        form = BackgroundChecksForm(organization=self.org)
        self.assertIn(f"cleared_{self.bct1.id}", form.fields)
        self.assertIn(f"obtained_{self.bct1.id}", form.fields)
        self.assertIn(f"cleared_{self.bct2.id}", form.fields)
        self.assertIn(f"obtained_{self.bct2.id}", form.fields)

    def test_form_save_creates_and_updates_checks(self):
        form = BackgroundChecksForm(
            data={
                f"cleared_{self.bct1.id}": "on",
                f"obtained_{self.bct1.id}": "2024-06-01",
            },
            organization=self.org,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save(student=self.student)
        self.assertEqual(self.student.background_checks.count(), 1)
        check = self.student.background_checks.first()
        self.assertEqual(check.check_type, self.bct1)
        self.assertTrue(check.cleared)
        self.assertEqual(check.obtained_date, datetime.date(2024, 6, 1))


class BackgroundCheckTypeFormTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Test Org", slug="test-org")

    def test_form_validation_and_save(self):
        form = BackgroundCheckTypeForm(
            data={
                "name": "State Clearance",
                "description": "Clearance description",
                "validity_years": 5,
                "applies_to_students": True,
                "applies_to_mentors": True,
                "applies_to_lead_mentors": True,
                "applies_to_parents": False,
                "applies_to_alumni": False,
                "student_min_age_by_date": 17,
                "student_min_age_cutoff_date": "2026-09-01",
                "student_min_age_during_program": 18,
                "is_active": True,
                "display_order": 1,
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        bct = form.save(commit=False)
        bct.organization = self.org
        bct.save()
        self.assertEqual(bct.name, "State Clearance")
        self.assertEqual(bct.student_min_age_by_date, 17)
        self.assertEqual(bct.student_min_age_cutoff_date, datetime.date(2026, 9, 1))
        self.assertEqual(bct.student_min_age_during_program, 18)

    def test_form_with_month_and_day_dropdowns(self):
        form = BackgroundCheckTypeForm(
            data={
                "name": "State Clearance Dropdown",
                "validity_years": 5,
                "applies_to_students": True,
                "student_min_age_by_date": 17,
                "student_min_age_cutoff_month": 10,
                "student_min_age_cutoff_day": 15,
                "display_order": 0,
                "is_active": True,
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        bct = form.save(commit=False)
        bct.organization = self.org
        bct.save()
        self.assertEqual(bct.student_min_age_cutoff_date, datetime.date(2000, 10, 15))
        self.assertEqual(bct.student_min_age_cutoff_month, 10)
        self.assertEqual(bct.student_min_age_cutoff_day, 15)


class PortalBackgroundCheckTypeViewTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Test Org", slug="test-org")
        self.user = User.objects.create_user(
            username="lead", password="password123"  # nosec B106
        )
        lead_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.user.groups.add(lead_group)
        self.client.login(username="lead", password="password123")  # nosec B106

    def test_create_background_check_type(self):
        url = reverse("portal_background_check_types")
        response = self.client.post(
            url,
            {
                "action": "add_type",
                "name": "Custom Clearance",
                "description": "Custom clearance instructions",
                "validity_years": 3,
                "applies_to_students": "on",
                "applies_to_mentors": "on",
                "student_min_age_by_date": 17,
                "student_min_age_cutoff_date": "2026-09-01",
                "student_min_age_during_program": 18,
                "display_order": 5,
            },
        )
        self.assertEqual(response.status_code, 302)
        bct = BackgroundCheckType.objects.filter(name="Custom Clearance").first()
        self.assertIsNotNone(bct)
        self.assertEqual(bct.validity_years, 3)
        self.assertTrue(bct.applies_to_students)
        self.assertEqual(bct.student_min_age_by_date, 17)
        self.assertEqual(bct.student_min_age_cutoff_date, datetime.date(2026, 9, 1))
        self.assertEqual(bct.student_min_age_during_program, 18)

    def test_create_background_check_type_with_month_day_dropdown(self):
        url = reverse("portal_background_check_types")
        response = self.client.post(
            url,
            {
                "action": "add_type",
                "name": "Dropdown Clearance",
                "validity_years": 5,
                "applies_to_students": "on",
                "student_min_age_by_date": 17,
                "student_min_age_cutoff_month": 9,
                "student_min_age_cutoff_day": 1,
            },
        )
        self.assertEqual(response.status_code, 302)
        bct = BackgroundCheckType.objects.filter(name="Dropdown Clearance").first()
        self.assertIsNotNone(bct)
        self.assertEqual(bct.student_min_age_cutoff_date, datetime.date(2000, 9, 1))
        self.assertEqual(bct.student_min_age_cutoff_month, 9)
        self.assertEqual(bct.student_min_age_cutoff_day, 1)

    def test_update_background_check_type(self):
        bct = BackgroundCheckType.objects.create(
            organization=self.org,
            name="Old Name",
            validity_years=5,
        )
        url = reverse("portal_background_check_types")
        response = self.client.post(
            url,
            {
                "action": "update_type",
                "type_id": bct.id,
                "name": "Updated Name",
                "validity_years": 2,
                "applies_to_mentors": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        bct.refresh_from_db()
        self.assertEqual(bct.name, "Updated Name")
        self.assertEqual(bct.validity_years, 2)
        self.assertTrue(bct.applies_to_mentors)
        self.assertFalse(bct.applies_to_students)

    def test_update_background_check_type_with_month_day_dropdown(self):
        bct = BackgroundCheckType.objects.create(
            organization=self.org,
            name="Clearance To Update",
            validity_years=5,
        )
        url = reverse("portal_background_check_types")
        response = self.client.post(
            url,
            {
                "action": "update_type",
                "type_id": bct.id,
                "name": "Clearance To Update",
                "applies_to_students": "on",
                "student_min_age_by_date": 17,
                "student_min_age_cutoff_month": 11,
                "student_min_age_cutoff_day": 30,
            },
        )
        self.assertEqual(response.status_code, 302)
        bct.refresh_from_db()
        self.assertEqual(bct.student_min_age_cutoff_date, datetime.date(2000, 11, 30))
        self.assertEqual(bct.student_min_age_cutoff_month, 11)
        self.assertEqual(bct.student_min_age_cutoff_day, 30)

    def test_toggle_background_check_type_active(self):
        bct = BackgroundCheckType.objects.create(
            organization=self.org,
            name="Toggle Me",
            is_active=True,
        )
        url = reverse("portal_background_check_types")
        self.client.post(
            url,
            {
                "action": "toggle_active",
                "type_id": bct.id,
            },
        )
        bct.refresh_from_db()
        self.assertFalse(bct.is_active)

    def test_delete_background_check_type(self):
        bct = BackgroundCheckType.objects.create(
            organization=self.org,
            name="Delete Me",
        )
        url = reverse("portal_background_check_types")
        self.client.post(
            url,
            {
                "action": "delete_type",
                "type_id": bct.id,
            },
        )
        self.assertFalse(BackgroundCheckType.objects.filter(id=bct.id).exists())
