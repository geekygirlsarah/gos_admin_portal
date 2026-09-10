from datetime import date, timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase

from programs.models import (
    Adult,
    AdultStudentRelationship,
    Enrollment,
    Program,
    School,
    Student,
)
from travel.models import (
    TravelCostOption,
    TravelDeparture,
    TravelEvent,
    TravelParentChaperoneSignup,
    TravelSignup,
)


class TravelEventModelTest(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Test School")
        self.student = Student.objects.create(
            legal_first_name="Test",
            last_name="Student",
            school=self.school,
            graduation_year=2027,
        )
        self.parent = Adult.objects.create(is_parent=True)
        self.event = TravelEvent.objects.create(
            name="Competition Trip",
            start_date=date.today() + timedelta(days=30),
            end_date=date.today() + timedelta(days=34),
        )

    def test_is_past_is_derived(self):
        self.assertFalse(self.event.is_past)
        self.event.end_date = date.today() - timedelta(days=1)
        self.assertTrue(self.event.is_past)

    def test_clean_rejects_end_before_start(self):
        bad = TravelEvent(
            name="Bad Trip",
            start_date=date(2026, 4, 5),
            end_date=date(2026, 4, 1),
        )
        with self.assertRaises(ValidationError):
            bad.clean()

    def test_departure_must_belong_to_event(self):
        other_event = TravelEvent.objects.create(
            name="Other Trip",
            start_date=date(2026, 5, 1),
            end_date=date(2026, 5, 4),
        )
        departure = TravelDeparture.objects.create(
            event=self.event, label="Bus 1", leave_date=date(2026, 4, 1)
        )
        signup = TravelSignup(
            student=self.student, event=other_event, departure=departure
        )
        with self.assertRaises(ValidationError):
            signup.clean()

    def test_total_cost_sums_selected_options(self):
        hotel = TravelCostOption.objects.create(
            event=self.event, name="Hotel", amount="300.00"
        )
        van = TravelCostOption.objects.create(
            event=self.event, name="Van", amount="50.00"
        )
        signup = TravelSignup.objects.create(student=self.student, event=self.event)
        signup.cost_items.set([hotel, van])
        self.assertEqual(signup.total_cost, 350)

    def test_approve_snapshot_and_clear_on_undo(self):
        option = TravelCostOption.objects.create(
            event=self.event, name="Hotel", amount="300.00"
        )
        signup = TravelSignup.objects.create(student=self.student, event=self.event)
        signup.cost_items.set([option])
        signup.approve(self.parent)
        signup.save()

        self.assertEqual(signup.status, TravelSignup.APPROVED)
        self.assertEqual(signup.approved_total, 300)
        self.assertEqual(signup.approved_by, self.parent)
        self.assertIsNotNone(signup.approved_at)

        signup.undo_decision()
        signup.save()
        self.assertEqual(signup.status, TravelSignup.INTERESTED)
        self.assertIsNone(signup.approved_total)
        self.assertIsNone(signup.approved_by)
        self.assertIsNone(signup.approved_at)

    def test_approve_requires_permission_acknowledgement(self):
        self.event.permission_form.name = "travel/permission_forms/1/form.pdf"
        signup = TravelSignup.objects.create(student=self.student, event=self.event)
        with self.assertRaises(ValidationError):
            signup.approve(self.parent)
        signup.approve(
            self.parent,
            permission_acknowledged=True,
            acknowledged_by="Mom",
        )
        signup.save()
        self.assertTrue(signup.permission_acknowledged)
        self.assertEqual(signup.permission_acknowledged_by, "Mom")
        self.assertIsNotNone(signup.permission_acknowledged_at)

    def test_approve_not_allowed_outside_interested(self):
        signup = TravelSignup.objects.create(student=self.student, event=self.event)
        signup.decline()
        signup.save()
        with self.assertRaises(ValidationError):
            signup.approve(self.parent)

    def test_withdraw_then_rejoin(self):
        signup = TravelSignup.objects.create(student=self.student, event=self.event)
        signup.withdraw()
        signup.save()
        self.assertEqual(signup.status, TravelSignup.WITHDRAWN)
        self.assertTrue(signup.can_be_signed_up_again)
        signup.rejoin()
        signup.save()
        self.assertEqual(signup.status, TravelSignup.INTERESTED)

    def test_can_be_withdrawn_only_when_interested_or_approved(self):
        signup = TravelSignup.objects.create(student=self.student, event=self.event)
        self.assertTrue(signup.can_be_withdrawn)
        signup.decline()
        signup.save()
        self.assertFalse(signup.can_be_withdrawn)

    def test_undo_only_for_approved_or_declined(self):
        signup = TravelSignup.objects.create(student=self.student, event=self.event)
        with self.assertRaises(ValidationError):
            signup.undo_decision()
        signup.decline()
        signup.save()
        signup.undo_decision()
        signup.save()
        self.assertEqual(signup.status, TravelSignup.INTERESTED)


class TravelParentChaperoneModelTest(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Test School")
        self.program = Program.objects.create(name="Test Program")
        # Active student in the program + link.
        self.student = Student.objects.create(
            legal_first_name="Test",
            last_name="Student",
            school=self.school,
            graduation_year=2027,
        )
        Enrollment.objects.create(
            student=self.student, program=self.program, active=True
        )
        # A star student who has graduated (should stay ineligible).
        self.grad_student = Student.objects.create(
            legal_first_name="Grad",
            last_name="Student",
            school=self.school,
            graduated=True,
        )
        self.parent = Adult.objects.create(
            is_parent=True, legal_first_name="Jane", last_name="Parent"
        )
        self.grad_parent = Adult.objects.create(
            is_parent=True, legal_first_name="Jo", last_name="GradParent"
        )
        AdultStudentRelationship.objects.create(adult=self.parent, student=self.student)
        AdultStudentRelationship.objects.create(
            adult=self.grad_parent, student=self.grad_student
        )
        self.event = TravelEvent.objects.create(
            name="Competition Trip",
            start_date=date.today() + timedelta(days=30),
            end_date=date.today() + timedelta(days=34),
        )

    def test_unique_together_adult_event(self):
        TravelParentChaperoneSignup.objects.create(adult=self.parent, event=self.event)
        dup = TravelParentChaperoneSignup(adult=self.parent, event=self.event)
        with self.assertRaises(ValidationError):
            dup.full_clean()

    def test_departure_must_belong_to_event(self):
        other_event = TravelEvent.objects.create(
            name="Other Trip",
            start_date=date(2026, 5, 1),
            end_date=date(2026, 5, 4),
        )
        departure = TravelDeparture.objects.create(
            event=other_event, label="Bus 1", leave_date=date(2026, 5, 1)
        )
        signup = TravelParentChaperoneSignup(
            adult=self.parent, event=self.event, departure=departure
        )
        with self.assertRaises(ValidationError):
            signup.clean()

    def test_eligible_adults_only_parents_with_active_students_in_program(self):
        eligible = set(
            TravelParentChaperoneSignup.eligible_adults(self.program).values_list(
                "pk", flat=True
            )
        )
        self.assertIn(self.parent.pk, eligible)
        # A non-parent adult and a parent whose student isn't in the program
        # are excluded.
        mentor = Adult.objects.create(is_mentor=True)
        self.assertNotIn(mentor.pk, eligible)
        self.assertNotIn(self.grad_parent.pk, eligible)
