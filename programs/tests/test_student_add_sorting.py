from django.test import TestCase

from programs.forms import (
    AddExistingStudentToProgramForm,
    PaymentForm,
    SlidingScaleForm,
)
from programs.models import Enrollment, Program, Student


class StudentSortingTest(TestCase):
    def setUp(self):
        self.program = Program.objects.create(name="Test Program", year=2024)
        # Create students in unsorted order
        # Use a mix of None and empty string for first_name to test Coalesce behavior
        self.s1 = Student.objects.create(
            legal_first_name="Zeno", last_name="A", first_name=None
        )
        self.s2 = Student.objects.create(
            legal_first_name="Alice", last_name="Z", first_name=""
        )
        self.s3 = Student.objects.create(
            legal_first_name="Bob", last_name="B", first_name=None
        )
        self.s4 = Student.objects.create(
            legal_first_name="Charlie", last_name="C", first_name=""
        )
        self.s5 = Student.objects.create(
            legal_first_name="adam", last_name="D", first_name=None
        )

    def test_add_existing_student_form_sorting(self):
        form = AddExistingStudentToProgramForm(program=self.program)
        students = list(form.fields["student"].queryset)

        # Expected order based on "first name then last name" (case-insensitive):
        # 1. adam (D)
        # 2. Alice (Z)
        # 3. Bob (B)
        # 4. Charlie (C)
        # 5. Zeno (A)

        def get_sort_name(s):
            return (s.first_name or s.legal_first_name).lower()

        names = [get_sort_name(s) for s in students]
        self.assertEqual(names, ["adam", "alice", "bob", "charlie", "zeno"])

    def test_payment_form_sorting(self):
        # Enroll students in the program
        Enrollment.objects.create(student=self.s1, program=self.program)
        Enrollment.objects.create(student=self.s2, program=self.program)
        Enrollment.objects.create(student=self.s3, program=self.program)
        Enrollment.objects.create(student=self.s4, program=self.program)
        Enrollment.objects.create(student=self.s5, program=self.program)

        form = PaymentForm(program=self.program)
        students = list(form.fields["student"].queryset)

        def get_sort_name(s):
            return (s.first_name or s.legal_first_name).lower()

        names = [get_sort_name(s) for s in students]
        self.assertEqual(names, ["adam", "alice", "bob", "charlie", "zeno"])

    def test_sliding_scale_form_sorting(self):
        # Enroll students in the program
        Enrollment.objects.create(student=self.s1, program=self.program)
        Enrollment.objects.create(student=self.s2, program=self.program)
        Enrollment.objects.create(student=self.s3, program=self.program)
        Enrollment.objects.create(student=self.s4, program=self.program)
        Enrollment.objects.create(student=self.s5, program=self.program)

        form = SlidingScaleForm(program=self.program)
        students = list(form.fields["student"].queryset)

        def get_sort_name(s):
            return (s.first_name or s.legal_first_name).lower()

        names = [get_sort_name(s) for s in students]
        self.assertEqual(names, ["adam", "alice", "bob", "charlie", "zeno"])
