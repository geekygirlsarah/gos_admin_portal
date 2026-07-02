"""Tests for the sliding scale application wizard.

TDD: these tests define the expected behavior before implementation.
"""

from __future__ import annotations

import datetime
import io

from django.contrib.auth.models import Group, Permission, User
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from applications.models import Application, SlidingScaleApplicationDocument
from programs.models import Adult, Enrollment, Program, SlidingScale, Student


def _make_program(name="Spring 2030", days_ahead=60):
    return Program.objects.create(
        name=name,
        start_date=timezone.localdate() + datetime.timedelta(days=days_ahead),
        end_date=timezone.localdate() + datetime.timedelta(days=days_ahead + 60),
        active=True,
    )


def _make_student(email="student@example.com"):
    return Student.objects.create(
        legal_first_name="Alice",
        first_name="Alice",
        last_name="Smith",
        date_of_birth=datetime.date(2010, 1, 1),
        personal_email=email,
    )


def _make_adult(email="parent@example.com"):
    return Adult.objects.create(
        first_name="Bob",
        last_name="Smith",
        personal_email=email,
        is_parent=True,
    )


def _make_reviewer():
    user = User.objects.create_user(
        username="reviewer", password="pass"  # nosec B106
    )
    perm = Permission.objects.get(codename="review_application")
    user.user_permissions.add(perm)
    return user


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class SlidingScaleWizardStep1Tests(TestCase):
    """Step 1: enter email to start sliding scale application."""

    def test_get_start_page_renders(self):
        resp = self.client.get(reverse("sliding_scale_start"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "sliding scale")

    def test_post_email_creates_application_and_redirects(self):
        resp = self.client.post(
            reverse("sliding_scale_start"),
            {"email": "parent@example.com"},
        )
        self.assertEqual(Application.objects.filter(applicant_type=Application.Type.SLIDING_SCALE).count(), 1)
        app = Application.objects.get(applicant_type=Application.Type.SLIDING_SCALE)
        self.assertRedirects(
            resp,
            reverse("sliding_scale_verify", kwargs={"app_id": app.application_id}),
        )

    def test_post_email_sends_otp(self):
        self.client.post(
            reverse("sliding_scale_start"),
            {"email": "parent@example.com"},
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("verification code", mail.outbox[0].body)

    def test_post_invalid_email_shows_error(self):
        resp = self.client.post(
            reverse("sliding_scale_start"),
            {"email": "not-an-email"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Application.objects.count(), 0)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class SlidingScaleWizardStep2OTPTests(TestCase):
    """Step 2: verify OTP."""

    def setUp(self):
        self.app = Application.objects.create(
            applicant_type=Application.Type.SLIDING_SCALE,
            email="parent@example.com",
            status=Application.Status.DRAFT,
            current_step=2,
        )
        self.code = self.app.issue_otp()

    def test_get_verify_page_renders(self):
        resp = self.client.get(
            reverse("sliding_scale_verify", kwargs={"app_id": self.app.application_id})
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "verification code")

    def test_valid_otp_verifies_and_redirects(self):
        resp = self.client.post(
            reverse("sliding_scale_verify", kwargs={"app_id": self.app.application_id}),
            {"code": self.code},
        )
        self.app.refresh_from_db()
        self.assertTrue(self.app.email_is_verified)
        self.assertRedirects(
            resp,
            reverse("sliding_scale_programs", kwargs={"app_id": self.app.application_id}),
        )

    def test_invalid_otp_shows_error(self):
        resp = self.client.post(
            reverse("sliding_scale_verify", kwargs={"app_id": self.app.application_id}),
            {"code": "000000"},
        )
        self.assertEqual(resp.status_code, 200)
        self.app.refresh_from_db()
        self.assertFalse(self.app.email_is_verified)

    def test_resend_otp_sends_new_code(self):
        resp = self.client.post(
            reverse("sliding_scale_resend", kwargs={"app_id": self.app.application_id})
        )
        self.assertRedirects(
            resp,
            reverse("sliding_scale_verify", kwargs={"app_id": self.app.application_id}),
        )
        self.assertEqual(len(mail.outbox), 1)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class SlidingScaleWizardStep3ProgramsTests(TestCase):
    """Step 3: confirm students and programs."""

    def setUp(self):
        self.program = _make_program()
        self.adult = _make_adult("parent@example.com")
        self.student = _make_student("student@example.com")
        self.student.primary_contact = self.adult
        self.student.save()
        Enrollment.objects.create(student=self.student, program=self.program)

        self.app = Application.objects.create(
            applicant_type=Application.Type.SLIDING_SCALE,
            email="parent@example.com",
            status=Application.Status.EMAIL_VERIFIED,
            email_verified_at=timezone.now(),
            current_step=3,
        )

    def test_get_shows_students_and_programs(self):
        resp = self.client.get(
            reverse("sliding_scale_programs", kwargs={"app_id": self.app.application_id})
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Alice Smith")
        self.assertContains(resp, self.program.name)

    def test_get_unverified_redirects_to_verify(self):
        self.app.email_verified_at = None
        self.app.status = Application.Status.DRAFT
        self.app.save()
        resp = self.client.get(
            reverse("sliding_scale_programs", kwargs={"app_id": self.app.application_id})
        )
        self.assertRedirects(
            resp,
            reverse("sliding_scale_verify", kwargs={"app_id": self.app.application_id}),
        )

    def test_post_confirms_and_redirects_to_income(self):
        resp = self.client.post(
            reverse("sliding_scale_programs", kwargs={"app_id": self.app.application_id}),
            {"confirm": "1"},
        )
        self.assertRedirects(
            resp,
            reverse("sliding_scale_income", kwargs={"app_id": self.app.application_id}),
        )


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class SlidingScaleWizardStep4IncomeTests(TestCase):
    """Step 4: enter income information."""

    def setUp(self):
        self.app = Application.objects.create(
            applicant_type=Application.Type.SLIDING_SCALE,
            email="parent@example.com",
            status=Application.Status.EMAIL_VERIFIED,
            email_verified_at=timezone.now(),
            current_step=4,
        )

    def test_get_income_page_renders(self):
        resp = self.client.get(
            reverse("sliding_scale_income", kwargs={"app_id": self.app.application_id})
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "income")

    def test_post_annual_income_saves_and_redirects(self):
        resp = self.client.post(
            reverse("sliding_scale_income", kwargs={"app_id": self.app.application_id}),
            {
                "adjusted_gross_income": "45000.00",
                "adjusted_monthly_income": "",
                "family_size": "4",
                "effective_date": "",
            },
        )
        self.app.refresh_from_db()
        data = self.app.data.get("ss_income", {})
        self.assertEqual(data["adjusted_gross_income"], "45000.00")
        self.assertEqual(data["family_size"], "4")
        self.assertRedirects(
            resp,
            reverse("sliding_scale_upload", kwargs={"app_id": self.app.application_id}),
        )

    def test_post_monthly_income_saves_and_redirects(self):
        resp = self.client.post(
            reverse("sliding_scale_income", kwargs={"app_id": self.app.application_id}),
            {
                "adjusted_gross_income": "",
                "adjusted_monthly_income": "3000.00",
                "family_size": "3",
                "effective_date": "2024-01-01",
            },
        )
        self.app.refresh_from_db()
        data = self.app.data.get("ss_income", {})
        self.assertEqual(data["adjusted_monthly_income"], "3000.00")
        self.assertRedirects(
            resp,
            reverse("sliding_scale_upload", kwargs={"app_id": self.app.application_id}),
        )

    def test_post_no_income_shows_error(self):
        resp = self.client.post(
            reverse("sliding_scale_income", kwargs={"app_id": self.app.application_id}),
            {
                "adjusted_gross_income": "",
                "adjusted_monthly_income": "",
                "family_size": "4",
                "effective_date": "",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFormError(
            resp.context["form"],
            None,
            "Please enter either an annual adjusted gross income or a monthly income.",
        )

    def test_post_no_family_size_shows_error(self):
        resp = self.client.post(
            reverse("sliding_scale_income", kwargs={"app_id": self.app.application_id}),
            {
                "adjusted_gross_income": "45000",
                "adjusted_monthly_income": "",
                "family_size": "",
                "effective_date": "",
            },
        )
        self.assertEqual(resp.status_code, 200)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class SlidingScaleWizardStep5UploadTests(TestCase):
    """Step 5: upload proof of income documents."""

    def setUp(self):
        self.app = Application.objects.create(
            applicant_type=Application.Type.SLIDING_SCALE,
            email="parent@example.com",
            status=Application.Status.EMAIL_VERIFIED,
            email_verified_at=timezone.now(),
            current_step=5,
            data={"ss_income": {"adjusted_gross_income": "45000", "family_size": "4"}},
        )

    def test_get_upload_page_renders(self):
        resp = self.client.get(
            reverse("sliding_scale_upload", kwargs={"app_id": self.app.application_id})
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "1040")

    def test_post_with_primary_doc_submits(self):
        fake_file = io.BytesIO(b"fake pdf content")
        fake_file.name = "tax_form.pdf"
        resp = self.client.post(
            reverse("sliding_scale_upload", kwargs={"app_id": self.app.application_id}),
            {
                "primary_document": fake_file,
            },
            format="multipart",
        )
        self.app.refresh_from_db()
        self.assertEqual(self.app.status, Application.Status.SUBMITTED)
        self.assertRedirects(
            resp,
            reverse("sliding_scale_submitted", kwargs={"app_id": self.app.application_id}),
        )

    def test_post_without_primary_doc_shows_error(self):
        resp = self.client.post(
            reverse("sliding_scale_upload", kwargs={"app_id": self.app.application_id}),
            {},
        )
        self.assertEqual(resp.status_code, 200)
        self.app.refresh_from_db()
        self.assertNotEqual(self.app.status, Application.Status.SUBMITTED)

    def test_submission_sends_lead_notification_email(self):
        fake_file = io.BytesIO(b"fake pdf content")
        fake_file.name = "tax_form.pdf"
        self.client.post(
            reverse("sliding_scale_upload", kwargs={"app_id": self.app.application_id}),
            {"primary_document": fake_file},
            format="multipart",
        )
        lead_emails = [m for m in mail.outbox if "leads@girlsofsteelrobotics.org" in m.to]
        self.assertGreater(len(lead_emails), 0)
        self.assertIn("sliding scale", lead_emails[0].body.lower())

    def test_submission_sends_confirmation_to_applicant(self):
        fake_file = io.BytesIO(b"fake pdf content")
        fake_file.name = "tax_form.pdf"
        self.client.post(
            reverse("sliding_scale_upload", kwargs={"app_id": self.app.application_id}),
            {"primary_document": fake_file},
            format="multipart",
        )
        applicant_emails = [m for m in mail.outbox if "parent@example.com" in m.to]
        self.assertGreater(len(applicant_emails), 0)

    def test_encrypted_document_stored(self):
        fake_file = io.BytesIO(b"fake pdf content")
        fake_file.name = "tax_form.pdf"
        self.client.post(
            reverse("sliding_scale_upload", kwargs={"app_id": self.app.application_id}),
            {"primary_document": fake_file},
            format="multipart",
        )
        self.assertEqual(
            SlidingScaleApplicationDocument.objects.filter(application=self.app).count(),
            1,
        )


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class SlidingScaleWizardSubmittedTests(TestCase):
    """Submitted confirmation page."""

    def setUp(self):
        self.app = Application.objects.create(
            applicant_type=Application.Type.SLIDING_SCALE,
            email="parent@example.com",
            status=Application.Status.SUBMITTED,
            email_verified_at=timezone.now(),
            submitted_at=timezone.now(),
            current_step=6,
        )

    def test_submitted_page_renders(self):
        resp = self.client.get(
            reverse("sliding_scale_submitted", kwargs={"app_id": self.app.application_id})
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "submitted")
        self.assertContains(resp, "lead mentor")


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class SlidingScaleReviewListTests(TestCase):
    """Sliding scale applications appear in the review list."""

    def setUp(self):
        self.reviewer = _make_reviewer()
        self.client.force_login(self.reviewer)
        self.ss_app = Application.objects.create(
            applicant_type=Application.Type.SLIDING_SCALE,
            email="parent@example.com",
            status=Application.Status.SUBMITTED,
            email_verified_at=timezone.now(),
            submitted_at=timezone.now(),
        )
        self.regular_app = Application.objects.create(
            applicant_type=Application.Type.PARENT,
            email="other@example.com",
            status=Application.Status.SUBMITTED,
            email_verified_at=timezone.now(),
            submitted_at=timezone.now(),
        )

    def test_review_list_shows_sliding_scale_app(self):
        resp = self.client.get(reverse("application_review_list"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, self.ss_app.application_id)

    def test_review_list_type_filter_sliding_scale(self):
        resp = self.client.get(
            reverse("application_review_list") + "?type=sliding_scale"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, self.ss_app.application_id)
        self.assertNotContains(resp, self.regular_app.application_id)

    def test_review_detail_shows_income_data(self):
        self.ss_app.data = {
            "ss_income": {
                "adjusted_gross_income": "45000",
                "family_size": "4",
            }
        }
        self.ss_app.save()
        resp = self.client.get(
            reverse("application_review_detail", kwargs={"app_id": self.ss_app.application_id})
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "45000")


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class SlidingScaleApprovalTests(TestCase):
    """Approving a sliding scale application creates SlidingScale records."""

    def setUp(self):
        self.reviewer = _make_reviewer()
        self.client.force_login(self.reviewer)

        self.program = _make_program()
        self.adult = _make_adult("parent@example.com")
        self.student = _make_student("student@example.com")
        self.student.primary_contact = self.adult
        self.student.save()
        Enrollment.objects.create(student=self.student, program=self.program)

        self.ss_app = Application.objects.create(
            applicant_type=Application.Type.SLIDING_SCALE,
            email="parent@example.com",
            status=Application.Status.SUBMITTED,
            email_verified_at=timezone.now(),
            submitted_at=timezone.now(),
            data={
                "ss_income": {
                    "adjusted_gross_income": "45000",
                    "family_size": "4",
                    "effective_date": "2024-01-01",
                }
            },
        )

    def test_approve_creates_sliding_scale_records(self):
        self.client.post(
            reverse(
                "application_review_approve",
                kwargs={"app_id": self.ss_app.application_id},
            )
        )
        self.ss_app.refresh_from_db()
        self.assertEqual(self.ss_app.status, Application.Status.APPROVED)
        self.assertTrue(
            SlidingScale.objects.filter(
                student=self.student, program=self.program, is_pending=True
            ).exists()
        )

    def test_approve_sends_confirmation_email(self):
        self.client.post(
            reverse(
                "application_review_approve",
                kwargs={"app_id": self.ss_app.application_id},
            )
        )
        applicant_emails = [m for m in mail.outbox if "parent@example.com" in m.to]
        self.assertGreater(len(applicant_emails), 0)
