from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import Group, Permission, User
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from programs.forms import SlidingScaleForm
from programs.models import (
    Adult,
    Enrollment,
    Program,
    SlidingScale,
    SlidingScaleSettings,
    Student,
    TaxForm,
)


class SlidingScaleApplicationTests(TestCase):
    """Covers the parent-facing sliding scale application flow: a Parent
    applies (across all of a student's programs, not tied to one), a Lead
    Mentor reviews/approves/declines it, uploaded documents are encrypted at
    rest and deleted once processed, and email notifications go out.
    """

    def setUp(self):
        self.program = Program.objects.create(name="Test Program", active=True)
        self.second_program = Program.objects.create(name="Second Program", active=True)
        self.student = Student.objects.create(
            legal_first_name="Test",
            last_name="Student",
            personal_email="student@example.com",
        )
        Enrollment.objects.create(student=self.student, program=self.program)
        Enrollment.objects.create(student=self.student, program=self.second_program)

        Group.objects.get_or_create(name="LeadMentor")
        self.lead_mentor_user = User.objects.create_user(
            username="lead", password="password", email="lead@example.com"
        )  # nosec B106
        self.lead_mentor_user.groups.add(Group.objects.get(name="LeadMentor"))

        self.mentor_user = User.objects.create_user(
            username="mentor", password="password", email="mentor@example.com"
        )  # nosec B106

        self.parent_adult = Adult.objects.create(
            first_name="Parent",
            last_name="User",
            personal_email="parent@example.com",
            is_parent=True,
            email_updates=True,
        )
        self.student.primary_contact = self.parent_adult
        self.student.save()

        self.parent_user = User.objects.create_user(
            username="parent", password="password"
        )  # nosec B106
        self.parent_adult.user = self.parent_user
        self.parent_adult.save()
        self.parent_adult.students.add(self.student)

        self.student_user = User.objects.create_user(
            username="student_login", password="password"
        )  # nosec B106
        self.student.user = self.student_user
        self.student.save()

        self.apply_url = reverse("sliding_scale_apply", args=[self.student.pk])

    # ------------------------------------------------------------------
    # Applying (Parent only)
    # ------------------------------------------------------------------

    def test_parent_can_apply_for_sliding_scale(self):
        self.client.login(username="parent", password="password")  # nosec B106

        mail.outbox = []
        pdf = SimpleUploadedFile(
            "tax_form.pdf", b"fake pdf content", content_type="application/pdf"
        )
        response = self.client.post(
            self.apply_url,
            {
                "family_size": 4,
                "adjusted_gross_income": "30000.00",
                "documents": pdf,
                "notes": "Lost my job this year.",
            },
            follow=False,
        )

        self.assertEqual(response.status_code, 302)
        application = SlidingScale.objects.get(student=self.student)
        self.assertEqual(application.status, SlidingScale.STATUS_PENDING)
        self.assertTrue(application.is_pending)
        self.assertEqual(application.family_size, 4)
        self.assertEqual(application.adjusted_gross_income, Decimal("30000.00"))
        self.assertEqual(application.applied_by, self.parent_adult)
        self.assertEqual(application.tax_forms.count(), 1)

        # Emails sent to parent + lead mentor
        self.assertEqual(len(mail.outbox), 2)

    def test_student_cannot_apply(self):
        self.client.login(username="student_login", password="password")  # nosec B106
        response = self.client.post(
            self.apply_url,
            {"family_size": 3, "adjusted_gross_income": "20000.00"},
            follow=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(SlidingScale.objects.filter(student=self.student).exists())

    def test_mentor_cannot_apply(self):
        self.client.login(username="mentor", password="password")  # nosec B106
        response = self.client.post(
            self.apply_url,
            {"family_size": 3, "adjusted_gross_income": "20000.00"},
            follow=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(SlidingScale.objects.filter(student=self.student).exists())

    def test_parent_cannot_apply_for_other_student(self):
        other_student = Student.objects.create(
            legal_first_name="Other", last_name="Student"
        )
        self.client.login(username="parent", password="password")  # nosec B106
        url = reverse("sliding_scale_apply", args=[other_student.pk])
        response = self.client.post(
            url, {"family_size": 3, "adjusted_gross_income": "20000.00"}, follow=False
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(SlidingScale.objects.filter(student=other_student).exists())

    def test_parent_cannot_apply_twice_while_pending(self):
        self.client.login(username="parent", password="password")  # nosec B106
        self.client.post(
            self.apply_url,
            {"family_size": 3, "adjusted_gross_income": "20000.00"},
        )
        self.assertEqual(SlidingScale.objects.filter(student=self.student).count(), 1)

        # GET redirects away instead of showing the form again
        response = self.client.get(self.apply_url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(SlidingScale.objects.filter(student=self.student).count(), 1)

    def test_unauthenticated_cannot_apply(self):
        response = self.client.post(
            self.apply_url,
            {"family_size": 3, "adjusted_gross_income": "20000.00"},
        )
        self.assertEqual(response.status_code, 302)  # Redirect to login

    def test_apply_page_includes_estimated_discount_settings(self):
        """The application page should expose the sliding scale calculation
        constants so JavaScript can show a live, non-binding discount
        estimate as the parent types (without submitting the values)."""
        self.client.login(username="parent", password="password")  # nosec B106
        response = self.client.get(self.apply_url)
        self.assertEqual(response.status_code, 200)

        settings_obj = SlidingScaleSettings.get_solo()
        self.assertEqual(response.context["settings_obj"], settings_obj)
        self.assertContains(response, "Estimated")
        self.assertContains(response, str(settings_obj.base_amount))
        self.assertContains(response, str(settings_obj.additional_member_amount))
        self.assertContains(response, str(settings_obj.low_multiplier))
        self.assertContains(response, str(settings_obj.high_multiplier))

    # ------------------------------------------------------------------
    # Admin entry form (from the Programs page) mirrors the apply layout
    # ------------------------------------------------------------------

    def test_sliding_scale_form_field_order_matches_apply_layout(self):
        """The Lead Mentor 'Add Sliding Scale' form should present the
        household questionnaire (family size + AGI) first, then the discount
        and date fields, so it reads like the parent-facing apply page."""
        form = SlidingScaleForm(program=self.program)
        self.assertEqual(
            list(form.fields.keys()),
            [
                "student",
                "family_size",
                "adjusted_gross_income",
                "percent",
                "date",
                "expiration_date",
                "notes",
            ],
        )

    def test_program_sliding_scale_create_page_includes_calculator(self):
        """The admin create page should expose the sliding scale calculation
        constants so JavaScript can show a live discount estimate (matching the
        parent apply page)."""
        perm = Permission.objects.get(codename="add_slidingscale")
        self.lead_mentor_user.user_permissions.add(perm)
        self.client.login(username="lead", password="password")  # nosec B106

        url = reverse("program_sliding_scale_create", args=[self.program.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        settings_obj = SlidingScaleSettings.get_solo()
        self.assertEqual(response.context["settings_obj"], settings_obj)
        self.assertContains(response, "Estimated")
        self.assertContains(response, str(settings_obj.base_amount))
        self.assertContains(response, str(settings_obj.additional_member_amount))
        self.assertContains(response, str(settings_obj.low_multiplier))
        self.assertContains(response, str(settings_obj.high_multiplier))
        self.assertContains(response, "Household Size")
        self.assertContains(response, "Adjusted Gross Income")

    def test_program_sliding_scale_create_page_uses_apply_style_fields(self):
        """The admin create page should render household + discount fields in
        Bootstrap form markup (not a bare database-row `form.as_p` dump)."""
        perm = Permission.objects.get(codename="add_slidingscale")
        self.lead_mentor_user.user_permissions.add(perm)
        self.client.login(username="lead", password="password")  # nosec B106

        url = reverse("program_sliding_scale_create", args=[self.program.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "form-control")
        self.assertContains(response, "sliding-scale-estimate")

    def test_lead_mentor_can_create_sliding_scale_with_household_info(self):
        """A Lead Mentor can create an approved sliding scale row from the
        program page, with the household questionnaire and discount data."""
        perm = Permission.objects.get(codename="add_slidingscale")
        self.lead_mentor_user.user_permissions.add(perm)
        self.client.login(username="lead", password="password")  # nosec B106

        url = reverse("program_sliding_scale_create", args=[self.program.pk])
        response = self.client.post(
            url,
            {
                "student": self.student.pk,
                "family_size": 4,
                "adjusted_gross_income": "30000.00",
                "percent": "50.00",
                "date": "2026-01-01",
                "expiration_date": "2027-01-01",
            },
            follow=False,
        )
        self.assertEqual(response.status_code, 302)
        sliding = SlidingScale.objects.get(student=self.student)
        self.assertEqual(sliding.status, SlidingScale.STATUS_APPROVED)
        self.assertEqual(sliding.family_size, 4)
        self.assertEqual(sliding.adjusted_gross_income, Decimal("30000.00"))
        self.assertEqual(sliding.percent, Decimal("50.00"))

    # ------------------------------------------------------------------
    # Withdrawing (Parent only, pending applications only)
    # ------------------------------------------------------------------

    def test_parent_can_withdraw_pending_application(self):
        self.client.login(username="parent", password="password")  # nosec B106
        pdf = SimpleUploadedFile(
            "tax_form.pdf", b"fake pdf content", content_type="application/pdf"
        )
        self.client.post(
            self.apply_url,
            {
                "family_size": 4,
                "adjusted_gross_income": "30000.00",
                "documents": pdf,
            },
        )
        application = SlidingScale.objects.get(student=self.student)

        withdraw_url = reverse("sliding_scale_withdraw", args=[application.pk])
        response = self.client.post(withdraw_url, follow=False)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(SlidingScale.objects.filter(pk=application.pk).exists())
        self.assertEqual(TaxForm.objects.count(), 0)

        # The parent can now re-apply for the same student.
        response = self.client.get(self.apply_url)
        self.assertEqual(response.status_code, 200)

    def test_parent_cannot_withdraw_other_students_application(self):
        other_student = Student.objects.create(
            legal_first_name="Other", last_name="Student"
        )
        other_application = SlidingScale.objects.create(
            student=other_student,
            family_size=2,
            adjusted_gross_income=Decimal("10000.00"),
            status=SlidingScale.STATUS_PENDING,
        )

        self.client.login(username="parent", password="password")  # nosec B106
        withdraw_url = reverse("sliding_scale_withdraw", args=[other_application.pk])
        response = self.client.post(withdraw_url, follow=False)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(SlidingScale.objects.filter(pk=other_application.pk).exists())

    def test_mentor_cannot_withdraw_application(self):
        application = self._create_pending_application()
        self.client.login(username="mentor", password="password")  # nosec B106
        withdraw_url = reverse("sliding_scale_withdraw", args=[application.pk])
        response = self.client.post(withdraw_url, follow=False)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(SlidingScale.objects.filter(pk=application.pk).exists())

    def test_cannot_withdraw_already_decided_application(self):
        application = self._create_pending_application()
        application.status = SlidingScale.STATUS_APPROVED
        application.percent = Decimal("50.00")
        application.save()

        self.client.login(username="parent", password="password")  # nosec B106
        withdraw_url = reverse("sliding_scale_withdraw", args=[application.pk])
        response = self.client.post(withdraw_url, follow=False)
        # get_object_or_404 raises Http404, which the project's custom handler
        # turns into a redirect (rather than a raw 404 page).
        self.assertEqual(response.status_code, 302)
        self.assertTrue(SlidingScale.objects.filter(pk=application.pk).exists())

    def test_unauthenticated_cannot_withdraw(self):
        application = self._create_pending_application()
        withdraw_url = reverse("sliding_scale_withdraw", args=[application.pk])
        response = self.client.post(withdraw_url, follow=False)
        self.assertEqual(response.status_code, 302)  # Redirect to login
        self.assertTrue(SlidingScale.objects.filter(pk=application.pk).exists())

    # ------------------------------------------------------------------
    # Encryption
    # ------------------------------------------------------------------

    def test_uploaded_documents_are_encrypted_at_rest(self):
        self.client.login(username="parent", password="password")  # nosec B106
        content = b"highly sensitive income data"
        pdf = SimpleUploadedFile("secret.pdf", content, content_type="application/pdf")
        self.client.post(
            self.apply_url,
            {
                "family_size": 4,
                "adjusted_gross_income": "30000.00",
                "documents": pdf,
            },
        )

        application = SlidingScale.objects.get(student=self.student)
        form_obj = application.tax_forms.first()
        with open(form_obj.file.path, "rb") as f:
            disk_content = f.read()
        self.assertNotEqual(disk_content, content)
        self.assertNotIn(content, disk_content)

        with form_obj.file.open("rb") as f:
            decrypted_content = f.read()
        self.assertEqual(decrypted_content, content)

    # ------------------------------------------------------------------
    # Review (Lead Mentor only)
    # ------------------------------------------------------------------

    def _create_pending_application(self):
        application = SlidingScale.objects.create(
            student=self.student,
            family_size=4,
            adjusted_gross_income=Decimal("30000.00"),
            status=SlidingScale.STATUS_PENDING,
            applied_by=self.parent_adult,
        )
        TaxForm.objects.create(
            sliding_scale=application,
            file=SimpleUploadedFile("t.pdf", b"content"),
        )
        return application

    def test_lead_mentor_can_view_decrypted_tax_form(self):
        """Lead Mentors should be able to view/download the *decrypted*
        contents of an uploaded tax form, not the raw encrypted bytes stored
        on disk."""
        application = self._create_pending_application()
        tax_form = application.tax_forms.first()
        self.client.login(username="lead", password="password")  # nosec B106

        url = reverse(
            "sliding_scale_tax_form_view",
            args=[application.pk, tax_form.pk],
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        content = b"".join(response.streaming_content)
        self.assertEqual(content, b"content")
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("inline", response["Content-Disposition"])

        download_response = self.client.get(url, {"download": "1"})
        self.assertEqual(download_response.status_code, 200)
        self.assertIn("attachment", download_response["Content-Disposition"])

    def test_mentor_cannot_view_tax_form(self):
        application = self._create_pending_application()
        tax_form = application.tax_forms.first()
        self.client.login(username="mentor", password="password")  # nosec B106

        url = reverse(
            "sliding_scale_tax_form_view",
            args=[application.pk, tax_form.pk],
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

    def test_lead_mentor_can_view_review_queue(self):
        self._create_pending_application()
        self.client.login(username="lead", password="password")  # nosec B106
        response = self.client.get(reverse("sliding_scale_review_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, str(self.student))

    def test_mentor_cannot_view_review_queue(self):
        self._create_pending_application()
        self.client.login(username="mentor", password="password")  # nosec B106
        response = self.client.get(reverse("sliding_scale_review_list"))
        self.assertEqual(response.status_code, 302)

    def test_lead_mentor_can_approve_application(self):
        application = self._create_pending_application()
        self.client.login(username="lead", password="password")  # nosec B106

        mail.outbox = []
        url = reverse("sliding_scale_review_decide", args=[application.pk])
        response = self.client.post(
            url,
            {
                "action": "approve",
                "percent": "62.50",
                "date": "2026-01-01",
                "expiration_date": "2027-01-01",
            },
            follow=False,
        )
        self.assertEqual(response.status_code, 302)

        application.refresh_from_db()
        self.assertEqual(application.status, SlidingScale.STATUS_APPROVED)
        self.assertEqual(application.percent, Decimal("62.50"))
        self.assertEqual(application.reviewed_by, self.lead_mentor_user)
        self.assertIsNotNone(application.reviewed_at)
        # Documents are deleted once processed
        self.assertEqual(application.tax_forms.count(), 0)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Approved", mail.outbox[0].subject)

    def test_approval_succeeds_even_if_tax_form_file_is_locked(self):
        """On Windows, a tax form file can still be locked by another
        process (e.g. it was just previewed/downloaded). Approving the
        application should not crash with a PermissionError — the file
        deletion failure should be tolerated and the review should still go
        through, with the SlidingScale/TaxForm records updated."""
        application = self._create_pending_application()
        self.client.login(username="lead", password="password")  # nosec B106

        with mock.patch(
            "django.db.models.fields.files.FieldFile.delete",
            side_effect=PermissionError(
                "[WinError 32] The process cannot access the file because "
                "it is being used by another process"
            ),
        ):
            url = reverse("sliding_scale_review_decide", args=[application.pk])
            response = self.client.post(
                url,
                {
                    "action": "approve",
                    "percent": "62.50",
                    "date": "2026-01-01",
                    "expiration_date": "2027-01-01",
                },
                follow=False,
            )

        self.assertEqual(response.status_code, 302)
        application.refresh_from_db()
        self.assertEqual(application.status, SlidingScale.STATUS_APPROVED)
        # The TaxForm DB record is still removed even though the physical
        # file could not be deleted.
        self.assertEqual(application.tax_forms.count(), 0)

    def test_lead_mentor_decline_requires_reason(self):
        application = self._create_pending_application()
        self.client.login(username="lead", password="password")  # nosec B106

        url = reverse("sliding_scale_review_decide", args=[application.pk])
        response = self.client.post(
            url, {"action": "decline", "decline_reason": ""}, follow=False
        )
        self.assertEqual(response.status_code, 302)
        application.refresh_from_db()
        self.assertEqual(application.status, SlidingScale.STATUS_PENDING)

    def test_lead_mentor_can_decline_application(self):
        application = self._create_pending_application()
        self.client.login(username="lead", password="password")  # nosec B106

        mail.outbox = []
        url = reverse("sliding_scale_review_decide", args=[application.pk])
        response = self.client.post(
            url,
            {
                "action": "decline",
                "decline_reason": "Income exceeds the sliding scale threshold.",
            },
            follow=False,
        )
        self.assertEqual(response.status_code, 302)

        application.refresh_from_db()
        self.assertEqual(application.status, SlidingScale.STATUS_DECLINED)
        self.assertEqual(
            application.decline_reason,
            "Income exceeds the sliding scale threshold.",
        )
        self.assertEqual(application.tax_forms.count(), 0)
        self.assertEqual(len(mail.outbox), 1)

    def test_mentor_cannot_decide_application(self):
        application = self._create_pending_application()
        self.client.login(username="mentor", password="password")  # nosec B106
        url = reverse("sliding_scale_review_decide", args=[application.pk])
        response = self.client.post(
            url, {"action": "approve", "percent": "50"}, follow=False
        )
        self.assertEqual(response.status_code, 302)
        application.refresh_from_db()
        self.assertEqual(application.status, SlidingScale.STATUS_PENDING)

    # ------------------------------------------------------------------
    # Cross-program application
    # ------------------------------------------------------------------

    def test_approved_sliding_scale_applies_across_all_programs(self):
        from programs.models import Fee
        from programs.utils import get_student_balance_data

        Fee.objects.create(program=self.program, name="Fee A", amount=Decimal("100.00"))
        Fee.objects.create(
            program=self.second_program, name="Fee B", amount=Decimal("100.00")
        )

        SlidingScale.objects.create(
            student=self.student,
            percent=Decimal("50.00"),
            status=SlidingScale.STATUS_APPROVED,
        )

        data_1 = get_student_balance_data(self.student, self.program)
        data_2 = get_student_balance_data(self.student, self.second_program)
        self.assertEqual(data_1["total_sliding"], Decimal("50"))
        self.assertEqual(data_2["total_sliding"], Decimal("50"))


class SlidingScaleSettingsCalculationTests(TestCase):
    def test_default_settings_calculation(self):
        settings_obj = SlidingScaleSettings.get_solo()
        # family_size=1: fed_base = 10150 + 1*5500 = 15650
        # low = 15650 * 1.5 = 23475; high = 15650 * 4 = 62600
        # agi = 43037.5 (midpoint) -> ratio 0.5 -> discount 50%
        midpoint_agi = (
            Decimal("23475.00") + (Decimal("62600.00") - Decimal("23475.00")) / 2
        )
        percent = settings_obj.compute_discount_percent(1, midpoint_agi)
        self.assertEqual(percent, Decimal("50.00"))

    def test_income_at_or_below_low_boundary_gives_full_discount(self):
        settings_obj = SlidingScaleSettings.get_solo()
        percent = settings_obj.compute_discount_percent(1, Decimal("0.00"))
        self.assertEqual(percent, Decimal("100.00"))

    def test_income_at_or_above_high_boundary_gives_no_discount(self):
        settings_obj = SlidingScaleSettings.get_solo()
        percent = settings_obj.compute_discount_percent(1, Decimal("1000000.00"))
        self.assertEqual(percent, Decimal("0.00"))

    def test_settings_are_editable(self):
        settings_obj = SlidingScaleSettings.get_solo()
        settings_obj.base_amount = Decimal("20000.00")
        settings_obj.additional_member_amount = Decimal("6000.00")
        settings_obj.low_multiplier = Decimal("1.00")
        settings_obj.high_multiplier = Decimal("3.00")
        settings_obj.save()

        reloaded = SlidingScaleSettings.get_solo()
        self.assertEqual(reloaded.base_amount, Decimal("20000.00"))
        self.assertEqual(reloaded.additional_member_amount, Decimal("6000.00"))
