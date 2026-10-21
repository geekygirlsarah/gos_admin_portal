from datetime import date
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core import mail
from django.test import TestCase
from django.urls import reverse

from audit.events import AuditEvent
from audit.models import AuditLog
from programs.models import Adult, Enrollment, Payment, Program, Student


class PaymentEditDeleteTests(TestCase):
    def setUp(self):
        # Create Lead Mentor
        self.lead_mentor_user = User.objects.create_superuser(
            username="lead_mentor",
            email="lead@example.com",
            password="password123",  # nosec B106
        )
        lm_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.lead_mentor_user.groups.add(lm_group)
        self.lead_mentor_adult = Adult.objects.create(
            user=self.lead_mentor_user,
            legal_first_name="Lead",
            last_name="Mentor",
            is_mentor=True,
        )

        # Create Mentor
        self.mentor_user = User.objects.create_user(
            username="mentor",
            email="mentor@example.com",
            password="password123",  # nosec B106
        )
        self.mentor_adult = Adult.objects.create(
            user=self.mentor_user,
            legal_first_name="Regular",
            last_name="Mentor",
            is_mentor=True,
        )

        # Create Parent and Student
        self.parent_user = User.objects.create_user(
            username="parent",
            email="parent@example.com",
            password="password123",  # nosec B106
        )
        self.parent_adult = Adult.objects.create(
            user=self.parent_user,
            legal_first_name="Jane",
            last_name="Doe",
            personal_email="parent@example.com",
            is_parent=True,
        )

        self.student_user = User.objects.create_user(
            username="student",
            email="student@example.com",
            password="password123",  # nosec B106
        )
        self.student = Student.objects.create(
            user=self.student_user,
            legal_first_name="Alice",
            last_name="Doe",
            primary_contact=self.parent_adult,
        )
        self.student.adults.add(self.parent_adult)

        # Programs
        self.program = Program.objects.create(
            name="Robotics 2026",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            active=True,
        )
        self.other_program = Program.objects.create(
            name="Art 2026",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            active=True,
        )

        self.enrollment = Enrollment.objects.create(
            student=self.student, program=self.program, active=True
        )

        # Existing payment
        self.payment = Payment.objects.create(
            student=self.student,
            program=self.program,
            amount=Decimal("150.00"),
            paid_on=date(2026, 3, 1),
            paid_via="check",
            check_number="1001",
            notes="Initial deposit",
        )

    def test_lead_mentor_can_access_edit_payment_view(self):
        self.client.login(username="lead_mentor", password="password123")  # nosec B106
        url = reverse("program_payment_edit", args=[self.program.pk, self.payment.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "programs/payment_form.html")
        self.assertContains(response, "Edit Payment")
        self.assertContains(response, "150.00")
        self.assertContains(response, "1001")

    def test_lead_mentor_can_edit_payment(self):
        self.client.login(username="lead_mentor", password="password123")  # nosec B106
        url = reverse("program_payment_edit", args=[self.program.pk, self.payment.pk])
        data = {
            "student": self.student.pk,
            "amount": "200.00",
            "paid_on": "2026-03-02",
            "paid_via": "credit_card",
            "check_number": "",
            "camp_hours": "",
            "notes": "Corrected amount",
        }
        response = self.client.post(url, data, follow=True)
        self.assertEqual(response.status_code, 200)

        self.payment.refresh_from_db()
        self.assertEqual(self.payment.amount, Decimal("200.00"))
        self.assertEqual(self.payment.paid_on, date(2026, 3, 2))
        self.assertEqual(self.payment.paid_via, "credit_card")
        self.assertIn(self.payment.check_number, ("", None))
        self.assertEqual(self.payment.notes, "Corrected amount")
        self.assertContains(response, "Payment updated successfully.")

    def test_edit_payment_does_not_send_duplicate_notification_email(self):
        mail.outbox.clear()
        self.client.login(username="lead_mentor", password="password123")  # nosec B106
        url = reverse("program_payment_edit", args=[self.program.pk, self.payment.pk])
        data = {
            "student": self.student.pk,
            "amount": "175.00",
            "paid_on": "2026-03-01",
            "paid_via": "check",
            "check_number": "1002",
            "notes": "Updated check number",
        }
        self.client.post(url, data)
        self.assertEqual(len(mail.outbox), 0)

    def test_lead_mentor_can_access_delete_payment_view(self):
        self.client.login(username="lead_mentor", password="password123")  # nosec B106
        url = reverse("program_payment_delete", args=[self.program.pk, self.payment.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "programs/payment_confirm_delete.html")
        self.assertContains(response, "Delete Payment")
        self.assertContains(response, "$150.00")
        self.assertContains(response, self.student.display_name)

    def test_lead_mentor_can_delete_payment(self):
        self.client.login(username="lead_mentor", password="password123")  # nosec B106
        url = reverse("program_payment_delete", args=[self.program.pk, self.payment.pk])
        response = self.client.post(url, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Payment.objects.filter(pk=self.payment.pk).exists())
        self.assertContains(response, "deleted")

        # Verify audit log recorded deletion
        audit_entry = AuditLog.objects.filter(
            event=AuditEvent.RECORD_DELETED,
            resource_type="Payment",
        ).first()
        self.assertIsNotNone(audit_entry)
        self.assertEqual(audit_entry.before.get("amount"), "150.00")

    def test_non_lead_mentor_cannot_edit_payment(self):
        for user, username in [
            (self.mentor_user, "mentor"),
            (self.parent_user, "parent"),
            (self.student_user, "student"),
        ]:
            with self.subTest(user=username):
                self.client.login(
                    username=username, password="password123"
                )  # nosec B106
                url = reverse(
                    "program_payment_edit", args=[self.program.pk, self.payment.pk]
                )
                response = self.client.get(url)
                self.assertIn(response.status_code, (302, 403))

                data = {
                    "student": self.student.pk,
                    "amount": "999.00",
                    "paid_on": "2026-03-01",
                    "paid_via": "cash",
                }
                post_resp = self.client.post(url, data)
                self.assertIn(post_resp.status_code, (302, 403))
                self.payment.refresh_from_db()
                self.assertEqual(self.payment.amount, Decimal("150.00"))

    def test_non_lead_mentor_cannot_delete_payment(self):
        for user, username in [
            (self.mentor_user, "mentor"),
            (self.parent_user, "parent"),
            (self.student_user, "student"),
        ]:
            with self.subTest(user=username):
                self.client.login(
                    username=username, password="password123"
                )  # nosec B106
                url = reverse(
                    "program_payment_delete", args=[self.program.pk, self.payment.pk]
                )
                response = self.client.get(url)
                self.assertIn(response.status_code, (302, 403))

                post_resp = self.client.post(url)
                self.assertIn(post_resp.status_code, (302, 403))
                self.assertTrue(Payment.objects.filter(pk=self.payment.pk).exists())

    def test_edit_payment_wrong_program_redirects_or_denies(self):
        self.client.login(username="lead_mentor", password="password123")  # nosec B106
        url = reverse(
            "program_payment_edit", args=[self.other_program.pk, self.payment.pk]
        )
        response = self.client.get(url)
        self.assertIn(response.status_code, (302, 404))

    def test_delete_payment_wrong_program_redirects_or_denies(self):
        self.client.login(username="lead_mentor", password="password123")  # nosec B106
        url = reverse(
            "program_payment_delete", args=[self.other_program.pk, self.payment.pk]
        )
        response = self.client.get(url)
        self.assertIn(response.status_code, (302, 404))

    def test_payment_detail_shows_action_buttons_conditionally(self):
        url = reverse("program_payment_detail", args=[self.program.pk, self.payment.pk])

        # Lead mentor sees Edit and Delete buttons
        self.client.login(username="lead_mentor", password="password123")  # nosec B106
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            reverse("program_payment_edit", args=[self.program.pk, self.payment.pk]),
        )
        self.assertContains(
            response,
            reverse("program_payment_delete", args=[self.program.pk, self.payment.pk]),
        )

        # Parent sees details and print, but not Edit or Delete
        self.client.login(username="parent", password="password123")  # nosec B106
        parent_resp = self.client.get(url)
        self.assertEqual(parent_resp.status_code, 200)
        self.assertNotContains(
            parent_resp,
            reverse("program_payment_edit", args=[self.program.pk, self.payment.pk]),
        )
        self.assertNotContains(
            parent_resp,
            reverse("program_payment_delete", args=[self.program.pk, self.payment.pk]),
        )

    def test_payment_form_allows_editing_for_deactivated_student(self):
        # Deactivate enrollment
        self.enrollment.active = False
        self.enrollment.save()

        self.client.login(username="lead_mentor", password="password123")  # nosec B106
        url = reverse("program_payment_edit", args=[self.program.pk, self.payment.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        data = {
            "student": self.student.pk,
            "amount": "180.00",
            "paid_on": "2026-03-01",
            "paid_via": "check",
            "check_number": "1001",
            "notes": "Edited while inactive",
        }
        post_resp = self.client.post(url, data, follow=True)
        self.assertEqual(post_resp.status_code, 200)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.amount, Decimal("180.00"))
        self.assertEqual(self.payment.notes, "Edited while inactive")
