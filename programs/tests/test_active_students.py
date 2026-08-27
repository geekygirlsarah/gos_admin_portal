"""Inactive students (graduated, or enrolled in a program with an inactive
enrollment) must not be mixed in with active students. They should either be
excluded entirely (dropdowns/selection lists) or separated out (program-scoped
student list pages), matching the behavior of the program detail page.
"""

from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from django.utils.crypto import get_random_string

from programs.forms import (
    AddExistingStudentToProgramForm,
    AdultForm,
    FeeAssignmentEditForm,
    PaymentForm,
    ProgramEmailBalancesForm,
    SlidingScaleForm,
)
from programs.models import Enrollment, Fee, FeeAssignment, Program, Student


class ActiveStudentDropdownTests(TestCase):
    """Inactive students must not appear in student dropdowns/selection lists."""

    def setUp(self):
        self.program = Program.objects.create(name="Season")
        self.other_program = Program.objects.create(name="Past Season")
        self.active = Student.objects.create(legal_first_name="Active", last_name="A")
        Enrollment.objects.create(
            student=self.active, program=self.program, active=True
        )
        self.dropped = Student.objects.create(
            preferred_first_name="Dropped", last_name="B"
        )
        Enrollment.objects.create(
            student=self.dropped, program=self.program, active=False
        )
        self.graduated = Student.objects.create(
            preferred_first_name="Grad", last_name="C", graduated=True
        )
        Enrollment.objects.create(
            student=self.graduated, program=self.program, active=True
        )
        self.other_program_student = Student.objects.create(
            preferred_first_name="Other", last_name="D"
        )
        Enrollment.objects.create(
            student=self.other_program_student,
            program=self.other_program,
            active=True,
        )
        self.unenrolled = Student.objects.create(
            preferred_first_name="New", last_name="E"
        )
        self.graduated_unenrolled = Student.objects.create(
            preferred_first_name="Old Grad", last_name="F", graduated=True
        )

    def test_add_existing_student_form_excludes_graduated(self):
        form = AddExistingStudentToProgramForm(program=self.program)
        qs = form.fields["student"].queryset
        self.assertNotIn(self.active, qs)
        self.assertIn(self.unenrolled, qs)
        self.assertNotIn(self.graduated, qs)
        self.assertNotIn(self.graduated_unenrolled, qs)

    def test_adult_form_students_field_excludes_graduated(self):
        lead_group, _ = Group.objects.get_or_create(name="LeadMentor")
        user = User.objects.create_user(
            username="admin", password="password"
        )  # nosec B106
        user.groups.add(lead_group)
        form = AdultForm(user=user)
        qs = form.fields["students"].queryset
        self.assertIn(self.active, qs)
        self.assertNotIn(self.graduated, qs)

    def test_payment_form_only_lists_active_enrolled_students(self):
        form = PaymentForm(program=self.program)
        qs = list(form.fields["student"].queryset)
        self.assertIn(self.active, qs)
        self.assertNotIn(self.dropped, qs)
        self.assertNotIn(self.graduated, qs)
        self.assertNotIn(self.other_program_student, qs)
        self.assertNotIn(self.unenrolled, qs)

    def test_sliding_scale_form_with_program_only_lists_active_enrolled(self):
        form = SlidingScaleForm(program=self.program)
        qs = list(form.fields["student"].queryset)
        self.assertIn(self.active, qs)
        self.assertNotIn(self.dropped, qs)
        self.assertNotIn(self.graduated, qs)
        self.assertNotIn(self.other_program_student, qs)
        self.assertNotIn(self.unenrolled, qs)

    def test_sliding_scale_form_without_program_excludes_graduated(self):
        form = SlidingScaleForm()
        qs = list(form.fields["student"].queryset)
        self.assertIn(self.active, qs)
        self.assertIn(self.dropped, qs)
        self.assertIn(self.unenrolled, qs)
        self.assertNotIn(self.graduated, qs)

    def test_email_balances_form_only_lists_active_enrolled(self):
        form = ProgramEmailBalancesForm(program=self.program)
        qs = list(form.fields["student"].queryset)
        self.assertIn(self.active, qs)
        self.assertNotIn(self.dropped, qs)
        self.assertNotIn(self.graduated, qs)
        self.assertNotIn(self.other_program_student, qs)
        self.assertNotIn(self.unenrolled, qs)

    def test_fee_assignment_form_only_lists_active_enrolled(self):
        fee = Fee.objects.create(program=self.program, name="Dues", amount="25.00")
        form = FeeAssignmentEditForm(program=self.program, fee=fee)
        qs = list(form.fields["students"].queryset)
        self.assertIn(self.active, qs)
        self.assertNotIn(self.dropped, qs)
        self.assertNotIn(self.graduated, qs)
        self.assertNotIn(self.other_program_student, qs)
        self.assertNotIn(self.unenrolled, qs)

    def test_fee_assignment_save_preserves_inactive_assignments(self):
        fee = Fee.objects.create(program=self.program, name="Dues", amount="25.00")
        FeeAssignment.objects.create(fee=fee, student=self.dropped)
        FeeAssignment.objects.create(fee=fee, student=self.graduated)
        form = FeeAssignmentEditForm(
            program=self.program,
            fee=fee,
            data={"students": [self.active.pk]},
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertTrue(
            FeeAssignment.objects.filter(fee=fee, student=self.active).exists()
        )
        self.assertTrue(
            FeeAssignment.objects.filter(fee=fee, student=self.dropped).exists()
        )
        self.assertTrue(
            FeeAssignment.objects.filter(fee=fee, student=self.graduated).exists()
        )


class ProgramStudentListSeparationTests(TestCase):
    """Program-scoped list pages must separate inactive students."""

    def setUp(self):
        self.password = "test_pass_123"  # nosec B105
        self.user = User.objects.create_user(
            username="leadmentor", password=self.password
        )
        self.lead_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.user.groups.add(self.lead_group)
        self.client.login(username="leadmentor", password=self.password)
        self.program = Program.objects.create(name="FLL 2025")
        self.active = Student.objects.create(legal_first_name="Active", last_name="A")
        self.active_enrollment = Enrollment.objects.create(
            student=self.active, program=self.program, active=True
        )
        self.dropped = Student.objects.create(
            preferred_first_name="Dropped", last_name="B"
        )
        self.dropped_enrollment = Enrollment.objects.create(
            student=self.dropped, program=self.program, active=False
        )
        self.graduated = Student.objects.create(
            preferred_first_name="Grad", last_name="C", graduated=True
        )
        self.graduated_enrollment = Enrollment.objects.create(
            student=self.graduated, program=self.program, active=True
        )

    def test_assignment_page_separates_inactive_students(self):
        url = reverse("program_assignment", args=[self.program.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        active_ids = {e.student_id for e in response.context["active_enrollments"]}
        inactive_ids = {e.student_id for e in response.context["inactive_enrollments"]}
        self.assertEqual(active_ids, {self.active.pk})
        self.assertEqual(inactive_ids, {self.dropped.pk, self.graduated.pk})

    def test_photo_grid_separates_inactive_students(self):
        url = reverse("program_student_photos", args=[self.program.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        active_ids = {e.student_id for e in response.context["active_enrollments"]}
        inactive_ids = {e.student_id for e in response.context["inactive_enrollments"]}
        self.assertEqual(active_ids, {self.active.pk})
        self.assertEqual(inactive_ids, {self.dropped.pk, self.graduated.pk})


class InactiveStudentTest(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            username="admin",
            password="password",
            email="admin@example.com",  # nosec B106
        )
        self.client.login(username="admin", password="password")  # nosec B106
        self.program = Program.objects.create(
            name="Test Program",
            active=True,
            start_date="2026-01-01",
            end_date="2026-12-31",
        )
        self.student = Student.objects.create(
            preferred_first_name="Active",
            last_name="Student",
            personal_email="active@example.com",
        )
        self.enrollment = Enrollment.objects.create(
            student=self.student, program=self.program, active=True
        )
        self.inactive_student = Student.objects.create(
            preferred_first_name="Inactive",
            last_name="Student",
            personal_email="inactive@example.com",
        )
        self.inactive_enrollment = Enrollment.objects.create(
            student=self.inactive_student, program=self.program, active=False
        )

    def test_program_detail_lists_inactive_enrollment_correctly(self):
        response = self.client.get(reverse("program_detail", args=[self.program.pk]))
        self.assertEqual(response.status_code, 200)
        active_enrollments = response.context["active_enrollments"]
        inactive_enrollments = response.context["inactive_enrollments"]
        self.assertIn(self.enrollment, active_enrollments)
        self.assertNotIn(self.inactive_enrollment, active_enrollments)
        self.assertIn(self.inactive_enrollment, inactive_enrollments)

    def test_messaging_excludes_inactive_enrollments(self):
        from django.core import mail

        url = reverse("program_email", args=[self.program.pk])
        data = {
            "program": self.program.pk,
            "recipient_groups": ["students"],
            "subject": "Test Subject",
            "body": "Test Body",
            "from_account": "DEFAULT",
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        all_recipients = []
        for m in mail.outbox:
            all_recipients.extend(m.to)
            all_recipients.extend(m.bcc)
        self.assertIn(self.student.personal_email, all_recipients)
        self.assertNotIn(self.inactive_student.personal_email, all_recipients)

    def test_enrollment_update_view_handles_active_flag(self):
        url = reverse("program_enrollment_update", args=[self.program.pk])
        data = {"enrollment_id": self.enrollment.id, "active": "false"}
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        self.enrollment.refresh_from_db()
        self.assertFalse(self.enrollment.active)


class InactiveStudentsDuesTest(TestCase):
    def setUp(self):
        password = get_random_string(32)
        self.user = User.objects.create_superuser(
            username="admin", password=password, email="admin@example.com"
        )
        self.client.login(username="admin", password=password)
        self.program = Program.objects.create(name="Test Program")

    def test_inactive_students_separated(self):
        student_active = Student.objects.create(
            legal_first_name="Active", last_name="Student"
        )
        Enrollment.objects.create(
            student=student_active, program=self.program, active=True
        )
        student_inactive = Student.objects.create(
            legal_first_name="Inactive", last_name="Student"
        )
        Enrollment.objects.create(
            student=student_inactive, program=self.program, active=False
        )
        student_graduated = Student.objects.create(
            legal_first_name="Graduated", last_name="Student", graduated=True
        )
        Enrollment.objects.create(
            student=student_graduated, program=self.program, active=True
        )
        url = reverse("program_dues_owed", args=[self.program.pk])
        response = self.client.get(url)
        active_rows = response.context["active_rows"]
        self.assertEqual(len(active_rows), 1)
        self.assertEqual(active_rows[0]["student"], student_active)
        inactive_rows = response.context["inactive_rows"]
        self.assertEqual(len(inactive_rows), 2)
        self.assertEqual(inactive_rows[0]["student"], student_graduated)
        self.assertEqual(inactive_rows[1]["student"], student_inactive)
        self.assertEqual(len(active_rows) + len(inactive_rows), 3)
