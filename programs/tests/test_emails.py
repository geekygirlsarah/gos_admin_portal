"""Email subsystem tests: balance emails, fee/payment/sliding scale notifications."""

from decimal import Decimal

from django.contrib.auth.models import User
from django.core import mail
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from programs.models import (
    Adult,
    Enrollment,
    Fee,
    Payment,
    Program,
    SlidingScale,
    Student,
)


class EmailBalancesTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(username="admin")
        self.client = Client()
        self.client.force_login(self.user)
        self.program = Program.objects.create(name="Test Program", active=True)
        self.s1 = Student.objects.create(first_name="Alice", last_name="Alpha")
        Enrollment.objects.create(student=self.s1, program=self.program)
        self.a1 = Adult.objects.create(
            first_name="P1",
            last_name="A1",
            personal_email="a1@example.com",
            email_updates=True,
        )
        self.s1.primary_contact = self.a1
        self.s1.save()

        self.s2 = Student.objects.create(first_name="Bob", last_name="Beta")
        Enrollment.objects.create(student=self.s2, program=self.program)
        self.a2 = Adult.objects.create(
            first_name="P2",
            last_name="A2",
            personal_email="a2@example.com",
            email_updates=True,
        )
        self.s2.primary_contact = self.a2
        self.s2.save()
        f1 = Fee.objects.create(program=self.program, name="Fee 1", amount=Decimal("100.00"))
        from programs.models import FeeAssignment

        FeeAssignment.objects.create(fee=f1, student=self.s2)

        self.s3 = Student.objects.create(first_name="Charlie", last_name="Gamma")
        Enrollment.objects.create(student=self.s3, program=self.program)
        self.a3 = Adult.objects.create(
            first_name="P3",
            last_name="A3",
            personal_email="a3@example.com",
            email_updates=True,
        )
        self.s3.primary_contact = self.a3
        self.s3.save()
        Payment.objects.create(
            student=self.s3,
            program=self.program,
            amount=Decimal("50.00"),
            paid_via="cash",
            paid_on=timezone.now().date(),
        )

    def test_view_get(self):
        url = reverse("program_dues_email", args=[self.program.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Email Balance Sheets")

    def test_view_post_all(self):
        url = reverse("program_dues_email", args=[self.program.id])
        data = {
            "program": self.program.id,
            "subject": "Test Subject",
            "recipient_filter": "all",
            "from_account": "DEFAULT",
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(
            response, reverse("program_dues_owed", args=[self.program.id])
        )

    def test_view_post_positive(self):
        url = reverse("program_dues_email", args=[self.program.id])
        data = {
            "program": self.program.id,
            "subject": "Test Subject",
            "recipient_filter": "positive",
            "from_account": "DEFAULT",
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)

    def test_view_post_individual(self):
        url = reverse("program_dues_email", args=[self.program.id])
        data = {
            "program": self.program.id,
            "subject": "Test Subject",
            "recipient_filter": "individual",
            "student": self.s1.id,
            "from_account": "DEFAULT",
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)

    def test_student_dropdown_labels_with_balances(self):
        url = reverse("program_dues_email", args=[self.program.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Alice Alpha ($0.00)")
        self.assertContains(response, "Bob Beta ($100.00)")
        self.assertContains(response, "Charlie Gamma ($-50.00)")


@override_settings(FILE_ENCRYPTION_KEY="ZmDfcTF7_60GrrY167zsiPd67pEvs0aGOv2oasOM1Pg=")
class AutoEmailNotificationsTest(TestCase):
    def setUp(self):
        self.program = Program.objects.create(name="Test Program", active=True)
        self.student = Student.objects.create(first_name="Alice", last_name="Alpha")
        self.parent = Adult.objects.create(
            first_name="Parent",
            last_name="Alpha",
            personal_email="parent@example.com",
            email_updates=True,
            is_parent=True,
        )
        self.student.primary_contact = self.parent
        self.student.save()
        Enrollment.objects.create(student=self.student, program=self.program)

    def test_fee_added_sends_email(self):
        mail.outbox = []
        Fee.objects.create(
            program=self.program, name="Registration Fee", amount=Decimal("50.00")
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.parent.personal_email])
        self.assertIn("Registration Fee", mail.outbox[0].subject)
        self.assertIn("50.00", mail.outbox[0].body)
        self.assertEqual(len(mail.outbox[0].alternatives), 1)
        html_content, mimetype = mail.outbox[0].alternatives[0]
        self.assertEqual(mimetype, "text/html")
        self.assertIn("New Fee Added</h1>", html_content)
        self.assertIn("$50.00", html_content)

    def test_payment_added_sends_email(self):
        Fee.objects.create(
            program=self.program, name="Registration Fee", amount=Decimal("100.00")
        )
        mail.outbox = []
        Payment.objects.create(
            student=self.student,
            program=self.program,
            amount=Decimal("40.00"),
            paid_on=timezone.now().date(),
            paid_via="cash",
            notes="Initial payment",
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.parent.personal_email])
        self.assertIn("Payment Recorded", mail.outbox[0].subject)
        self.assertIn("40.00", mail.outbox[0].body)
        self.assertIn("Initial payment", mail.outbox[0].body)
        self.assertIn("60.00", mail.outbox[0].body)
        self.assertEqual(len(mail.outbox[0].alternatives), 1)
        html_content, mimetype = mail.outbox[0].alternatives[0]
        self.assertEqual(mimetype, "text/html")
        self.assertIn("$40.00", html_content)
        self.assertIn("Initial payment", html_content)
        self.assertIn("$60.00", html_content)

    def test_sliding_scale_approved_directly_sends_email(self):
        mail.outbox = []
        SlidingScale.objects.create(
            student=self.student,
            percent=Decimal("50.00"),
            status=SlidingScale.STATUS_APPROVED,
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.parent.personal_email])
        self.assertIn("Sliding Scale Application Approved", mail.outbox[0].subject)
        self.assertIn("50.00%", mail.outbox[0].body)
        self.assertEqual(len(mail.outbox[0].alternatives), 1)
        html_content, mimetype = mail.outbox[0].alternatives[0]
        self.assertEqual(mimetype, "text/html")
        self.assertIn("50.00%", html_content)

    def test_sliding_scale_application_submitted_notifies_parent_and_lead_mentor(self):
        mail.outbox = []
        SlidingScale.objects.create(
            student=self.student,
            family_size=4,
            adjusted_gross_income=Decimal("30000.00"),
            status=SlidingScale.STATUS_PENDING,
        )
        self.assertEqual(len(mail.outbox), 2)
        subjects = [m.subject for m in mail.outbox]
        self.assertTrue(
            any("Sliding Scale Application Submitted" in s for s in subjects)
        )
        self.assertTrue(
            any("New Sliding Scale Application to Review" in s for s in subjects)
        )

    def test_sliding_scale_declined_notifies_parent_and_deletes_documents(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        from programs.models import TaxForm

        application = SlidingScale.objects.create(
            student=self.student,
            family_size=2,
            adjusted_gross_income=Decimal("60000.00"),
            status=SlidingScale.STATUS_PENDING,
        )
        TaxForm.objects.create(
            sliding_scale=application, file=SimpleUploadedFile("t.pdf", b"content")
        )
        self.assertEqual(application.tax_forms.count(), 1)
        mail.outbox = []
        application.status = SlidingScale.STATUS_DECLINED
        application.decline_reason = "Income exceeds the sliding scale threshold."
        application.save()
        self.assertEqual(application.tax_forms.count(), 0)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Sliding Scale Application Update", mail.outbox[0].subject)
        self.assertIn("Income exceeds the sliding scale threshold.", mail.outbox[0].body)

    def test_no_email_if_email_updates_false(self):
        self.parent.email_updates = False
        self.parent.save()
        mail.outbox = []
        Fee.objects.create(
            program=self.program, name="Another Fee", amount=Decimal("10.00")
        )
        self.assertEqual(len(mail.outbox), 0)
