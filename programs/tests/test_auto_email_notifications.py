from decimal import Decimal

from django.core import mail
from django.test import TestCase, override_settings
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


@override_settings(FILE_ENCRYPTION_KEY="ZmDfcTF7_60GrrY167zsiPd67pEvs0aGOv2oasOM1Pg=")
class AutoEmailNotificationsTest(TestCase):
    def setUp(self):
        self.program = Program.objects.create(name="Test Program", active=True)

        # Student and Parent
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
        # Clear outbox
        mail.outbox = []

        # Add a fee to the program
        fee = Fee.objects.create(
            program=self.program, name="Registration Fee", amount=Decimal("50.00")
        )

        # Assert email was sent
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.parent.personal_email])
        self.assertIn("Registration Fee", mail.outbox[0].subject)
        self.assertIn("50.00", mail.outbox[0].body)

        # Assert HTML version exists
        self.assertEqual(len(mail.outbox[0].alternatives), 1)
        html_content, mimetype = mail.outbox[0].alternatives[0]
        self.assertEqual(mimetype, "text/html")
        self.assertIn("New Fee Added</h1>", html_content)
        self.assertIn("$50.00", html_content)

    def test_payment_added_sends_email(self):
        # Add a fee first so balance is interesting
        Fee.objects.create(
            program=self.program, name="Registration Fee", amount=Decimal("100.00")
        )

        # Clear outbox
        mail.outbox = []

        # Add a payment
        Payment.objects.create(
            student=self.student,
            program=self.program,
            amount=Decimal("40.00"),
            paid_on=timezone.now().date(),
            paid_via="cash",
            notes="Initial payment",
        )

        # Assert email was sent
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.parent.personal_email])
        self.assertIn("Payment Recorded", mail.outbox[0].subject)
        self.assertIn("40.00", mail.outbox[0].body)
        self.assertIn("Initial payment", mail.outbox[0].body)
        # Balance should be 100 - 40 = 60
        self.assertIn("60.00", mail.outbox[0].body)

        # Assert HTML version exists
        self.assertEqual(len(mail.outbox[0].alternatives), 1)
        html_content, mimetype = mail.outbox[0].alternatives[0]
        self.assertEqual(mimetype, "text/html")
        self.assertIn("$40.00", html_content)
        self.assertIn("Initial payment", html_content)
        self.assertIn("$60.00", html_content)

    def test_sliding_scale_approved_directly_sends_email(self):
        # Clear outbox
        mail.outbox = []

        # A Lead Mentor creates an already-approved sliding scale directly
        # (not via the parent application flow) — the parent should still be
        # notified that it's been approved.
        SlidingScale.objects.create(
            student=self.student,
            percent=Decimal("50.00"),
            status=SlidingScale.STATUS_APPROVED,
        )

        # Assert email was sent
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.parent.personal_email])
        self.assertIn("Sliding Scale Application Approved", mail.outbox[0].subject)
        self.assertIn("50.00%", mail.outbox[0].body)

        # Assert HTML version exists
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
        self.assertIn(
            "Income exceeds the sliding scale threshold.", mail.outbox[0].body
        )

    def test_no_email_if_email_updates_false(self):
        self.parent.email_updates = False
        self.parent.save()

        mail.outbox = []
        Fee.objects.create(
            program=self.program, name="Another Fee", amount=Decimal("10.00")
        )
        self.assertEqual(len(mail.outbox), 0)
