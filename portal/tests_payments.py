import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from programs.models import (
    Adult,
    AdultStudentRelationship,
    Enrollment,
    Fee,
    Payment,
    Program,
    SlidingScale,
    Student,
)


class ParentPaymentsViewTests(TestCase):
    def setUp(self):
        password = "password123"  # nosec B105
        self.parent_user = User.objects.create_user(
            username="parent_user", password=password
        )
        self.parent = Adult.objects.create(
            user=self.parent_user,
            first_name="Parent",
            last_name="User",
            is_parent=True,
        )
        self.student = Student.objects.create(first_name="Student", last_name="One")
        AdultStudentRelationship.objects.create(
            adult=self.parent,
            student=self.student,
            relationship_to_student="parent",
        )

        self.mentor_user = User.objects.create_user(
            username="mentor_user", password=password
        )
        Adult.objects.create(
            user=self.mentor_user,
            first_name="Mentor",
            last_name="User",
            is_mentor=True,
        )

        self.student_user = User.objects.create_user(
            username="student_user", password=password
        )
        Student.objects.create(
            user=self.student_user, first_name="Student", last_name="User"
        )

        self.lead_mentor_user = User.objects.create_user(
            username="lead_mentor", password=password
        )
        lead_mentor_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.lead_mentor_user.groups.add(lead_mentor_group)

        self.program_a = Program.objects.create(name="Program A", active=True)
        self.program_b = Program.objects.create(name="Program B", active=True)
        Enrollment.objects.create(
            student=self.student, program=self.program_a, active=True
        )
        Enrollment.objects.create(
            student=self.student, program=self.program_b, active=True
        )

        Fee.objects.create(
            program=self.program_a,
            name="Program A Fee",
            amount=Decimal("100.00"),
            effective_date=datetime.date.today(),
        )
        Fee.objects.create(
            program=self.program_b,
            name="Program B Fee",
            amount=Decimal("50.00"),
            effective_date=datetime.date.today(),
        )
        Payment.objects.create(
            student=self.student,
            program=self.program_a,
            amount=Decimal("20.00"),
            paid_on=datetime.date.today(),
        )

    def test_parent_sees_payments_nav_link(self):
        self.client.login(username="parent_user", password="password123")  # nosec B106
        response = self.client.get(reverse("profile_dashboard"))
        self.assertContains(response, reverse("parent_payments"))
        self.assertContains(response, ">Payments<", html=False)

    def test_mentor_and_student_do_not_see_payments_nav_link(self):
        self.client.login(username="mentor_user", password="password123")  # nosec B106
        mentor_response = self.client.get(reverse("profile_dashboard"))
        self.assertNotContains(mentor_response, reverse("parent_payments"))

        self.client.login(username="student_user", password="password123")  # nosec B106
        student_response = self.client.get(reverse("profile_dashboard"))
        self.assertNotContains(student_response, reverse("parent_payments"))

    def test_parent_payments_summary_shows_students_then_programs_and_grand_total(self):
        self.client.login(username="parent_user", password="password123")  # nosec B106
        response = self.client.get(reverse("parent_payments"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Student One")
        self.assertContains(response, "Program A")
        self.assertContains(response, "Program B")
        self.assertContains(response, "View Balance")
        self.assertContains(
            response,
            reverse(
                "program_student_balance", args=[self.program_a.pk, self.student.pk]
            ),
        )
        self.assertEqual(response.context["grand_total"], Decimal("130.00"))
        self.assertEqual(len(response.context["student_rows"]), 1)
        self.assertEqual(len(response.context["student_rows"][0]["program_rows"]), 2)

    def test_parent_payments_shows_withdraw_button_for_pending_sliding_scale(self):
        application = SlidingScale.objects.create(
            student=self.student,
            family_size=3,
            adjusted_gross_income=Decimal("25000.00"),
            status=SlidingScale.STATUS_PENDING,
            applied_by=self.parent,
        )

        self.client.login(username="parent_user", password="password123")  # nosec B106
        response = self.client.get(reverse("parent_payments"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sliding Scale")
        self.assertContains(response, "Application pending review by a Lead Mentor.")
        withdraw_url = reverse("sliding_scale_withdraw", args=[application.pk])
        self.assertContains(response, withdraw_url)
        self.assertContains(response, "Withdraw Application")

    def test_students_mentors_and_lead_mentors_cannot_access_parent_payments_views(
        self,
    ):
        summary_url = reverse("parent_payments")

        self.client.login(username="student_user", password="password123")  # nosec B106
        self.assertEqual(self.client.get(summary_url).status_code, 302)

        self.client.login(username="mentor_user", password="password123")  # nosec B106
        self.assertEqual(self.client.get(summary_url).status_code, 302)

        self.client.login(username="lead_mentor", password="password123")  # nosec B106
        self.assertEqual(self.client.get(summary_url).status_code, 302)
