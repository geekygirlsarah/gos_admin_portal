"""Combined fee create/edit form with inline applicability.

A fee's name/amount/dates and which students it applies to (applicability)
are now edited together on one page. On create, parents are emailed only for
the fee's applicable students (matching the selection), and the user is
returned to the fee list with a confirmation message.
"""

from decimal import Decimal

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase
from django.urls import reverse

from programs.models import (
    Adult,
    Enrollment,
    Fee,
    FeeAssignment,
    Program,
    Student,
)


class FeeCombinedFormTests(TestCase):
    def setUp(self):
        self.program = Program.objects.create(name="Test Program", active=True)

        self.student_a = Student.objects.create(
            preferred_first_name="Alice", last_name="Alpha"
        )
        self.student_b = Student.objects.create(
            preferred_first_name="Bob", last_name="Beta"
        )
        Enrollment.objects.create(student=self.student_a, program=self.program)
        Enrollment.objects.create(student=self.student_b, program=self.program)

        self.parent_a = Adult.objects.create(
            legal_first_name="Pat",
            last_name="Alpha",
            is_parent=True,
            login_enabled=True,
            email_updates=True,
            personal_email="parent_a@example.com",
        )
        self.student_a.primary_contact = self.parent_a
        self.student_a.save()

        self.parent_b = Adult.objects.create(
            legal_first_name="Pat",
            last_name="Beta",
            is_parent=True,
            login_enabled=True,
            email_updates=True,
            personal_email="parent_b@example.com",
        )
        self.student_b.primary_contact = self.parent_b
        self.student_b.save()

        self.lead = User.objects.create_superuser(
            username="lead", password="password123"
        )  # nosec B106
        self.client.force_login(self.lead)

        self.create_url = reverse("program_fee_create", args=[self.program.pk])

    def _post_fee(self, student_ids, **extra):
        data = {
            "program": self.program.pk,
            "name": "Registration Fee",
            "amount": "100.00",
            "effective_date": "2026-01-01",
            "due_date": "2026-02-01",
            "students": student_ids,
        }
        data.update(extra)
        return self.client.post(self.create_url, data, follow=True)

    def test_create_with_selected_students_assigns_and_emails_only_them(self):
        mail.outbox = []
        response = self._post_fee([self.student_a.pk])
        self.assertRedirects(
            response, reverse("program_fee_select", args=[self.program.pk])
        )

        fee = Fee.objects.get(program=self.program, name="Registration Fee")
        self.assertEqual(
            list(fee.assignments.values_list("student_id", flat=True)),
            [
                self.student_a.pk,
            ],
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.parent_a.personal_email])
        self.assertNotIn(
            self.parent_b.personal_email, mail.outbox[0].to + mail.outbox[0].bcc
        )

    def test_create_no_selection_applies_to_everyone_and_emails_all(self):
        mail.outbox = []
        self._post_fee([])

        fee = Fee.objects.get(program=self.program, name="Registration Fee")
        self.assertEqual(fee.assignments.count(), 0)
        self.assertEqual(len(mail.outbox), 2)
        to_emails = {m.to[0] for m in mail.outbox}
        self.assertEqual(
            to_emails, {self.parent_a.personal_email, self.parent_b.personal_email}
        )

    def test_create_redirects_to_fee_list_with_confirmation(self):
        response = self.client.post(
            self.create_url,
            {
                "program": self.program.pk,
                "name": "Registration Fee",
                "amount": "100.00",
            },
            follow=True,
        )
        self.assertRedirects(
            response, reverse("program_fee_select", args=[self.program.pk])
        )
        self.assertContains(response, "was created")

    def test_create_does_not_emit_fee_assignment_duplicate_emails(self):
        """Creating a fee with a selection must not email all parents via the
        Fee post_save signal AND the selected student again via assignments."""
        mail.outbox = []
        self._post_fee([self.student_a.pk])
        self.assertEqual(len(mail.outbox), 1)

    def test_form_renders_students_field_with_enrolled_students(self):
        response = self.client.get(self.create_url)
        self.assertContains(response, 'name="students"')
        self.assertContains(response, "Save Fee")
        self.assertContains(response, 'value="%s"' % self.student_a.pk)
        self.assertContains(response, 'value="%s"' % self.student_b.pk)
        self.assertContains(response, "dual-listbox")
        self.assertContains(response, "css/dual-listbox.css")
        self.assertContains(response, "js/dual-listbox.js")
        self.assertNotContains(response, "No active, enrolled students")

    def test_form_shows_message_when_no_eligible_students(self):
        self.student_a.delete()
        self.student_b.delete()
        response = self.client.get(self.create_url)
        self.assertContains(response, "No active, enrolled students")

    def test_invalid_post_rerenders_full_form(self):
        """Submitting without a name/amount re-renders the form and must keep
        all fields (and their labels) visible so the user can correct them."""
        response = self.client.post(
            self.create_url,
            {"program": self.program.pk, "amount": "100.00"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="name"')
        self.assertContains(response, 'name="amount"')
        self.assertContains(response, "Name:")
        self.assertContains(response, "Amount:")
        self.assertContains(response, "This field is required.")
        self.assertContains(response, 'value="%s"' % self.student_a.pk)
        self.assertNotContains(response, "No active, enrolled students")
        self.assertFalse(Fee.objects.filter(program=self.program, name="").exists())


class FeeCombinedFormEditTests(TestCase):
    def setUp(self):
        self.program = Program.objects.create(name="Edit Program", active=True)
        self.student = Student.objects.create(
            preferred_first_name="Alice", last_name="Alpha"
        )
        Enrollment.objects.create(student=self.student, program=self.program)
        self.parent = Adult.objects.create(
            legal_first_name="Pat",
            last_name="Alpha",
            is_parent=True,
            login_enabled=True,
            email_updates=True,
            personal_email="parent@example.com",
        )
        self.student.primary_contact = self.parent
        self.student.save()

        self.fee = Fee.objects.create(
            program=self.program,
            name="Dues",
            amount=Decimal("50.00"),
        )
        self.lead = User.objects.create_superuser(
            username="lead", password="password123"
        )  # nosec B106
        self.client.force_login(self.lead)

        self.edit_url = reverse("program_fee_edit", args=[self.program.pk, self.fee.pk])

    def test_edit_saves_assignments_and_redirects_to_list(self):
        mail.outbox = []
        response = self.client.post(
            self.edit_url,
            {
                "program": self.program.pk,
                "name": "Dues",
                "amount": "55.00",
                "students": [self.student.pk],
            },
            follow=True,
        )
        self.fee.refresh_from_db()
        self.assertEqual(self.fee.amount, Decimal("55.00"))
        self.assertTrue(
            FeeAssignment.objects.filter(fee=self.fee, student=self.student).exists()
        )
        self.assertRedirects(
            response, reverse("program_fee_select", args=[self.program.pk])
        )
        self.assertContains(response, "Fee updated")
