"""Sliding scale tests: application flow, tax forms, settings calculation,
date-restricted application, and balance sheet integration."""

import datetime
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import Group, Permission, User
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from programs.forms import SlidingScaleForm
from programs.models import (
    Adult,
    Enrollment,
    Fee,
    Program,
    SlidingScale,
    SlidingScaleSettings,
    Student,
    TaxForm,
)


@override_settings(FILE_ENCRYPTION_KEY="ZmDfcTF7_60GrrY167zsiPd67pEvs0aGOv2oasOM1Pg=")
class SlidingScaleApplicationTests(TestCase):
    """Covers the parent-facing sliding scale application flow: a Parent
    applies, a Lead Mentor reviews/approves/declines it, uploaded documents
    are encrypted at rest and deleted once processed, and email notifications
    go out.
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
            legal_first_name="Parent",
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
        response = self.client.get(self.apply_url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(SlidingScale.objects.filter(student=self.student).count(), 1)

    def test_unauthenticated_cannot_apply(self):
        response = self.client.post(
            self.apply_url,
            {"family_size": 3, "adjusted_gross_income": "20000.00"},
        )
        self.assertEqual(response.status_code, 302)

    def test_apply_page_includes_estimated_discount_settings(self):
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

    def test_sliding_scale_form_field_order_matches_apply_layout(self):
        form = SlidingScaleForm()
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

    def _login_lead_with_add_permission(self):
        perm = Permission.objects.get(codename="add_slidingscale")
        self.lead_mentor_user.user_permissions.add(perm)
        self.client.login(username="lead", password="password")  # nosec B106

    def test_sliding_scale_create_page_includes_calculator(self):
        self._login_lead_with_add_permission()
        url = reverse("sliding_scale_create")
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

    def test_sliding_scale_create_page_uses_apply_style_fields(self):
        self._login_lead_with_add_permission()
        url = reverse("sliding_scale_create")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "form-control")
        self.assertContains(response, "sliding-scale-estimate")

    def test_lead_mentor_can_create_sliding_scale_with_household_info(self):
        self._login_lead_with_add_permission()
        url = reverse("sliding_scale_create")
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
        self.assertEqual(response.url, reverse("sliding_scale_review_list"))
        sliding = SlidingScale.objects.get(student=self.student)
        self.assertEqual(sliding.status, SlidingScale.STATUS_APPROVED)
        self.assertEqual(sliding.family_size, 4)
        self.assertEqual(sliding.adjusted_gross_income, Decimal("30000.00"))
        self.assertEqual(sliding.percent, Decimal("50.00"))

    def test_review_list_shows_add_button(self):
        self.client.login(username="lead", password="password")  # nosec B106
        response = self.client.get(reverse("sliding_scale_review_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Add Sliding Scale")
        self.assertContains(response, reverse("sliding_scale_create"))

    def test_program_detail_has_no_add_sliding_scale_button(self):
        self.client.login(username="lead", password="password")  # nosec B106
        response = self.client.get(reverse("program_detail", args=[self.program.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Add Sliding Scale")

    def test_review_list_shows_edit_links(self):
        pending = SlidingScale.objects.create(
            student=self.student,
            family_size=3,
            adjusted_gross_income=Decimal("20000.00"),
            status=SlidingScale.STATUS_PENDING,
        )
        decided = SlidingScale.objects.create(
            student=self.student,
            percent=Decimal("50.00"),
            status=SlidingScale.STATUS_APPROVED,
            reviewed_by=self.lead_mentor_user,
            reviewed_at=datetime.datetime.now(),
        )
        self.client.login(username="lead", password="password")  # nosec B106
        response = self.client.get(reverse("sliding_scale_review_list"))
        self.assertEqual(response.status_code, 200)
        edit_pending = reverse("sliding_scale_edit", args=[pending.pk])
        edit_decided = reverse("sliding_scale_edit", args=[decided.pk])
        self.assertContains(response, edit_pending)
        self.assertContains(response, edit_decided)

    def test_lead_mentor_can_edit_sliding_scale(self):
        perm = Permission.objects.get(codename="change_slidingscale")
        self.lead_mentor_user.user_permissions.add(perm)
        self.client.login(username="lead", password="password")  # nosec B106
        sliding = SlidingScale.objects.create(
            student=self.student,
            family_size=2,
            adjusted_gross_income=Decimal("15000.00"),
            percent=Decimal("75.00"),
            date=datetime.date(2026, 1, 1),
            status=SlidingScale.STATUS_APPROVED,
        )
        url = reverse("sliding_scale_edit", args=[sliding.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        response = self.client.post(
            url,
            {
                "student": self.student.pk,
                "family_size": 4,
                "adjusted_gross_income": "30000.00",
                "percent": "50.00",
                "date": "2026-06-01",
                "expiration_date": "2027-01-01",
            },
            follow=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("sliding_scale_review_list"))
        sliding.refresh_from_db()
        self.assertEqual(sliding.family_size, 4)
        self.assertEqual(sliding.adjusted_gross_income, Decimal("30000.00"))
        self.assertEqual(sliding.percent, Decimal("50.00"))
        self.assertEqual(sliding.date, datetime.date(2026, 6, 1))
        self.assertEqual(sliding.expiration_date, datetime.date(2027, 1, 1))

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
        self.assertEqual(response.status_code, 302)
        self.assertTrue(SlidingScale.objects.filter(pk=application.pk).exists())

    def test_unauthenticated_cannot_withdraw(self):
        application = self._create_pending_application()
        withdraw_url = reverse("sliding_scale_withdraw", args=[application.pk])
        response = self.client.post(withdraw_url, follow=False)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(SlidingScale.objects.filter(pk=application.pk).exists())

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
        self.assertEqual(application.tax_forms.count(), 0)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Approved", mail.outbox[0].subject)

    def test_approval_succeeds_even_if_tax_form_file_is_locked(self):
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

    def test_approved_sliding_scale_applies_across_all_programs(self):
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


@override_settings(FILE_ENCRYPTION_KEY="ZmDfcTF7_60GrrY167zsiPd67pEvs0aGOv2oasOM1Pg=")
class EncryptedFileRepeatedOpenTests(TestCase):
    """Reproducers for EncryptedFileDescriptor.decrypted_open bugs."""

    def setUp(self):
        self.student = Student.objects.create(
            legal_first_name="Test",
            last_name="Student",
            personal_email="student@example.com",
        )
        self.sliding_scale = SlidingScale.objects.create(student=self.student)

    def _create_tax_form(self, content):
        return TaxForm.objects.create(
            sliding_scale=self.sliding_scale,
            file=SimpleUploadedFile("doc.pdf", content),
        )

    def test_repeated_opens_return_full_content(self):
        content = b"repeated open content"
        tax_form = self._create_tax_form(content)
        with tax_form.file.open("rb") as f:
            first = f.read()
        with tax_form.file.open("rb") as f:
            second = f.read()
        self.assertEqual(first, content)
        self.assertEqual(second, content)

    def test_open_closes_underlying_storage_handle(self):
        content = b"handle leak content"
        tax_form = self._create_tax_form(content)
        with tax_form.file.open("rb") as f:
            self.assertEqual(f.read(), content)
        self.assertTrue(tax_form.file.closed)

    def test_legacy_plaintext_file_readable_through_fallback(self):
        from django.core.files.base import ContentFile

        content = b"legacy plaintext not encrypted"
        tax_form = TaxForm.objects.create(sliding_scale=self.sliding_scale)
        tax_form.file.save("legacy.txt", ContentFile(content), save=True)
        with tax_form.file.open("rb") as f:
            self.assertEqual(f.read(), content)


class SlidingScaleTimeRestrictedTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username="admin",
            password="password",
            email="admin@example.com",  # nosec B106
        )
        self.client.login(username="admin", password="password")  # nosec B106

    def test_sliding_scale_respects_date(self):
        program = Program.objects.create(name="Time Program 2")
        student = Student.objects.create(legal_first_name="Time", last_name="Student 2")
        Enrollment.objects.create(student=student, program=program)
        Fee.objects.create(
            program=program,
            name="Past Fee",
            amount=Decimal("100.00"),
            effective_date=datetime.date(2026, 1, 1),
        )
        Fee.objects.create(
            program=program,
            name="Future Fee",
            amount=Decimal("100.00"),
            effective_date=datetime.date(2026, 3, 1),
        )
        SlidingScale.objects.create(
            student=student,
            percent=Decimal("50.00"),
            date=datetime.date(2026, 2, 1),
        )
        url = reverse("program_student_balance", args=[program.pk, student.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        entries = response.context["entries"]
        sliding_scale_entry = next(e for e in entries if e["type"] == "Sliding Scale")
        self.assertEqual(sliding_scale_entry["amount"], Decimal("0.00"))
        past_fee = next(e for e in entries if e["name"] == "Past Fee")
        future_fee = next(e for e in entries if e["name"] == "Future Fee")
        self.assertEqual(past_fee["adjusted_amount"], Decimal("100.00"))
        self.assertEqual(future_fee["adjusted_amount"], Decimal("50.00"))
        self.assertEqual(response.context["balance"], Decimal("150.00"))

    def test_sliding_scale_only_applies_to_overlapping_programs(self):
        from programs.utils import get_student_balance_data

        student = Student.objects.create(
            legal_first_name="Overlap", last_name="Student"
        )
        overlapping = Program.objects.create(
            name="Overlapping Program",
            start_date=datetime.date(2026, 3, 1),
            end_date=datetime.date(2026, 6, 30),
        )
        non_overlapping = Program.objects.create(
            name="Non-Overlapping Program",
            start_date=datetime.date(2025, 1, 1),
            end_date=datetime.date(2025, 12, 31),
        )
        inactive_but_overlapping = Program.objects.create(
            name="Inactive Overlapping Program",
            active=False,
            start_date=datetime.date(2026, 4, 1),
            end_date=datetime.date(2026, 7, 31),
        )
        for program in (overlapping, non_overlapping, inactive_but_overlapping):
            Enrollment.objects.create(student=student, program=program)
            Fee.objects.create(
                program=program,
                name="Program Fee",
                amount=Decimal("100.00"),
                effective_date=datetime.date(2026, 3, 15),
            )
        SlidingScale.objects.create(
            student=student,
            percent=Decimal("50.00"),
            date=datetime.date(2026, 2, 1),
            expiration_date=datetime.date(2026, 12, 31),
        )
        data_overlap = get_student_balance_data(student, overlapping)
        data_no_overlap = get_student_balance_data(student, non_overlapping)
        data_inactive = get_student_balance_data(student, inactive_but_overlapping)
        self.assertEqual(data_overlap["total_sliding"], Decimal("50"))
        self.assertEqual(data_no_overlap["total_sliding"], Decimal("0"))
        self.assertEqual(data_inactive["total_sliding"], Decimal("50"))

    def test_sliding_scale_no_date_applies_to_all(self):
        program = Program.objects.create(name="All Fees Program")
        student = Student.objects.create(legal_first_name="All", last_name="Fees")
        Enrollment.objects.create(student=student, program=program)
        Fee.objects.create(
            program=program,
            name="Fee 1",
            amount=Decimal("100.00"),
            effective_date=datetime.date(2026, 1, 1),
        )
        Fee.objects.create(
            program=program,
            name="Fee 2",
            amount=Decimal("100.00"),
            effective_date=datetime.date(2026, 3, 1),
        )
        SlidingScale.objects.create(
            student=student, percent=Decimal("50.00"), date=None
        )
        url = reverse("program_student_balance", args=[program.pk, student.pk])
        response = self.client.get(url)
        sliding_scale_entry = next(
            e for e in response.context["entries"] if e["type"] == "Sliding Scale"
        )
        self.assertEqual(sliding_scale_entry["amount"], Decimal("0.00"))
        fee1 = next(e for e in response.context["entries"] if e["name"] == "Fee 1")
        fee2 = next(e for e in response.context["entries"] if e["name"] == "Fee 2")
        self.assertEqual(fee1["adjusted_amount"], Decimal("50.00"))
        self.assertEqual(fee2["adjusted_amount"], Decimal("50.00"))


class BalanceSheetSlidingScaleTest(TestCase):
    def setUp(self):
        password = "password"  # nosec B105
        self.user = User.objects.create_superuser(
            username="admin",
            password=password,
            email="admin@example.com",
        )
        self.client.login(username="admin", password=password)

    def test_adjusted_rate_column_presence_and_values(self):
        program = Program.objects.create(name="Sliding Program")
        student = Student.objects.create(
            legal_first_name="Sliding", last_name="Student"
        )
        Enrollment.objects.create(student=student, program=program)
        Fee.objects.create(
            program=program,
            name="Early Fee",
            amount=Decimal("100.00"),
            effective_date=datetime.date(2026, 1, 1),
        )
        Fee.objects.create(
            program=program,
            name="Late Fee",
            amount=Decimal("200.00"),
            effective_date=datetime.date(2026, 3, 1),
        )
        SlidingScale.objects.create(
            student=student,
            percent=Decimal("50.00"),
            date=datetime.date(2026, 2, 1),
        )
        url = reverse("program_student_balance", args=[program.pk, student.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        entries = response.context["entries"]
        early_fee = next(e for e in entries if e["name"] == "Early Fee")
        self.assertEqual(early_fee["adjusted_amount"], Decimal("100.00"))
        late_fee = next(e for e in entries if e["name"] == "Late Fee")
        self.assertEqual(late_fee["adjusted_amount"], Decimal("100.00"))
        sliding_entry = next(e for e in entries if e["type"] == "Sliding Scale")
        self.assertEqual(sliding_entry["amount"], Decimal("0.00"))
        self.assertEqual(response.context["total_sliding"], Decimal("100.00"))
        self.assertEqual(response.context["balance"], Decimal("200.00"))


class SlidingScaleIndexTests(TestCase):
    """Verify the composite index backing get_active_sliding_scale() exists."""

    def test_active_lookup_composite_index_exists(self):
        from django.db import connection

        table = SlidingScale._meta.db_table
        with connection.cursor() as cursor:
            if connection.vendor == "postgresql":
                cursor.execute(
                    "SELECT indexname FROM pg_indexes WHERE tablename = %s",
                    [table],
                )
            else:
                cursor.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = %s",
                    [table],
                )
            index_names = {row[0] for row in cursor.fetchall()}
        self.assertIn("slidingscale_active_lookup_idx", index_names)
