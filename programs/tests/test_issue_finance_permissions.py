from django.contrib.auth.models import User, Group
from django.test import TestCase
from django.urls import reverse
from programs.models import Adult, Student, Program, RolePermission

class FinancePermissionTests(TestCase):
    def setUp(self):
        # Lead Mentor
        self.lead_mentor_user = User.objects.create_user(username="lead_mentor", password="password123")
        lm_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.lead_mentor_user.groups.add(lm_group)

        # Mentor
        self.mentor_user = User.objects.create_user(username="mentor_user", password="password123")
        Adult.objects.create(user=self.mentor_user, first_name="Mentor", last_name="User", is_mentor=True)

        # Parent
        self.parent_user = User.objects.create_user(username="parent_user", password="password123")
        self.parent_adult = Adult.objects.create(user=self.parent_user, first_name="Parent", last_name="User", is_parent=True)

        # Student
        self.student_user = User.objects.create_user(username="student_user", password="password123")
        self.student_profile = Student.objects.create(user=self.student_user, first_name="Student", last_name="User")

        # Alumni
        self.alumni_user = User.objects.create_user(username="alumni_user", password="password123")
        Adult.objects.create(user=self.alumni_user, first_name="Alumni", last_name="User", is_alumni=True)

        self.program = Program.objects.create(name="Test Program", active=True)
        
        # Enroll student in program
        from programs.models import Enrollment
        Enrollment.objects.create(student=self.student_profile, program=self.program, active=True)
        
        # Link student to parent
        from programs.models import AdultStudentRelationship
        AdultStudentRelationship.objects.create(adult=self.parent_adult, student=self.student_profile, relationship_to_student="parent")

    def test_mentor_cannot_view_balance_sheet(self):
        self.client.login(username="mentor_user", password="password123")
        url = reverse("program_student_balance", args=[self.program.pk, self.student_profile.pk])
        response = self.client.get(url)
        # Mentors are already blocked from 'payments' section
        self.assertEqual(response.status_code, 302)

    def test_student_cannot_view_own_balance_sheet(self):
        # Current implementation might allow this if 'payments' can_read defaults to True for Students
        self.client.login(username="student_user", password="password123")
        url = reverse("program_student_balance", args=[self.program.pk, self.student_profile.pk])
        response = self.client.get(url)
        # Should be blocked
        self.assertEqual(response.status_code, 302)

    def test_alumni_cannot_view_balance_sheet(self):
        self.client.login(username="alumni_user", password="password123")
        url = reverse("program_student_balance", args=[self.program.pk, self.student_profile.pk])
        response = self.client.get(url)
        # Should be blocked
        self.assertEqual(response.status_code, 302)

    def test_parent_can_view_child_balance_sheet(self):
        self.client.login(username="parent_user", password="password123")
        url = reverse("program_student_balance", args=[self.program.pk, self.student_profile.pk])
        response = self.client.get(url)
        # User said: "Parents should see balances for the moment"
        self.assertEqual(response.status_code, 200)

    def test_parent_cannot_view_other_student_balance_sheet(self):
        other_student = Student.objects.create(first_name="Other", last_name="Student")
        self.client.login(username="parent_user", password="password123")
        url = reverse("program_student_balance", args=[self.program.pk, other_student.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

    def test_mentor_cannot_view_payments_create(self):
        self.client.login(username="mentor_user", password="password123")
        url = reverse("program_payment_create", args=[self.program.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

    def test_mentor_cannot_view_dues_owed(self):
        self.client.login(username="mentor_user", password="password123")
        url = reverse("program_dues_owed", args=[self.program.pk])
        response = self.client.get(url)
        # Should be blocked
        self.assertEqual(response.status_code, 302)

    def test_mentor_cannot_view_email_balances(self):
        self.client.login(username="mentor_user", password="password123")
        url = reverse("program_dues_email", args=[self.program.pk])
        response = self.client.get(url)
        # Should be blocked
        self.assertEqual(response.status_code, 302)
