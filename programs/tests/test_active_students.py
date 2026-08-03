"""Inactive students (graduated, or enrolled in a program with an inactive
enrollment) must not be mixed in with active students. They should either be
excluded entirely (dropdowns/selection lists) or separated out (program-scoped
student list pages), matching the behavior of the program detail page.
"""

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

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

        self.active = Student.objects.create(first_name="Active", last_name="A")
        Enrollment.objects.create(
            student=self.active, program=self.program, active=True
        )

        # Enrolled in this program but the enrollment was marked inactive
        self.dropped = Student.objects.create(first_name="Dropped", last_name="B")
        Enrollment.objects.create(
            student=self.dropped, program=self.program, active=False
        )

        # Graduated (student-level inactive)
        self.graduated = Student.objects.create(
            first_name="Grad", last_name="C", graduated=True
        )
        Enrollment.objects.create(
            student=self.graduated, program=self.program, active=True
        )

        # Enrolled in a different program, not this one
        self.other_program_student = Student.objects.create(
            first_name="Other", last_name="D"
        )
        Enrollment.objects.create(
            student=self.other_program_student,
            program=self.other_program,
            active=True,
        )

        # Not enrolled anywhere yet
        self.unenrolled = Student.objects.create(first_name="New", last_name="E")

        # Graduated AND not enrolled in this program (still shouldn't be offered)
        self.graduated_unenrolled = Student.objects.create(
            first_name="Old Grad", last_name="F", graduated=True
        )

    def test_add_existing_student_form_excludes_graduated(self):
        form = AddExistingStudentToProgramForm(program=self.program)
        qs = form.fields["student"].queryset
        # Already-enrolled students are excluded by the form itself
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
    """Program-scoped list pages must separate inactive students, like the
    program detail page already does."""

    def setUp(self):
        self.password = "test_pass_123"  # nosec B105
        self.user = User.objects.create_user(
            username="leadmentor", password=self.password
        )
        self.lead_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.user.groups.add(self.lead_group)
        self.client.login(username="leadmentor", password=self.password)

        self.program = Program.objects.create(name="FLL 2025")

        self.active = Student.objects.create(first_name="Active", last_name="A")
        self.active_enrollment = Enrollment.objects.create(
            student=self.active, program=self.program, active=True
        )

        self.dropped = Student.objects.create(first_name="Dropped", last_name="B")
        self.dropped_enrollment = Enrollment.objects.create(
            student=self.dropped, program=self.program, active=False
        )

        self.graduated = Student.objects.create(
            first_name="Grad", last_name="C", graduated=True
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
