from django.contrib.auth.models import User, Group, Permission
from django.test import TestCase
from django.urls import reverse
from decimal import Decimal
from programs.models import Program, Student, Enrollment, Fee, SlidingScale, FeeAssignment

class SlidingScaleDiscountTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="mentor", password="password")
        group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.user.groups.add(group)
        # Grant permissions for payments and sliding scale
        perm_payments = Permission.objects.get(codename="view_payment") # Assuming this exists or using DynamicReadPermissionMixin
        # In this project, permissions seem to be handled by DynamicReadPermissionMixin which checks for 'LeadMentor' or similar.
        self.client.login(username="mentor", password="password")

    def test_sliding_scale_only_includes_applicable_fees(self):
        program = Program.objects.create(name="Summer Camp")
        student = Student.objects.create(legal_first_name="John", last_name="Doe")
        Enrollment.objects.create(student=student, program=program)

        # Fee 1: Global fee (applies to everyone)
        fee1 = Fee.objects.create(program=program, name="Base Fee", amount=Decimal("100.00"))
        
        # Fee 2: Assigned fee (applies only to ANOTHER student)
        other_student = Student.objects.create(legal_first_name="Jane", last_name="Smith")
        Enrollment.objects.create(student=other_student, program=program)
        fee2 = Fee.objects.create(program=program, name="Optional Equipment", amount=Decimal("50.00"))
        FeeAssignment.objects.create(fee=fee2, student=other_student)

        # Sliding scale: 50% discount for John Doe
        SlidingScale.objects.create(student=student, program=program, percent=Decimal("50.00"))

        url = reverse("program_student_balance", args=[program.pk, student.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        # Total fees for John should be 100.00
        # Discount should be 50% of 100.00 = 50.00
        # If the bug exists, total_fees_for_discount will be 150.00 and discount will be 75.00
        
        entries = response.context['entries']
        sliding_scale_entry = next(e for e in entries if e['type'] == 'Sliding Scale')
        
        # We expect discount to be 50.00 (amount = -50.00)
        self.assertEqual(sliding_scale_entry['amount'], Decimal("-50.00"), 
                         f"Discount should be 50.00, but got {sliding_scale_entry['amount']}")
        
        # Also check balance
        # total_fees = 100, total_sliding = 50, total_payments = 0 -> balance = 50
        self.assertEqual(response.context['balance'], Decimal("50.00"))
