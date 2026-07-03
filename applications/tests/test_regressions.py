from __future__ import annotations

import datetime
import re

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from applications.forms import StudentInfoForm
from applications.models import Application
from applications.services import send_otp_email
from applications.tests.test_review import _reviewer_user
from programs.models import Program


class SubmittedNamesReproductionTest(TestCase):
    """Test displaying student and parent names on the application thanks page."""

    def setUp(self):
        self.program = Program.objects.create(
            name="Girls of Steel Program",
            active=True,
        )

    def test_submitted_page_shows_student_and_parent_names(self):
        application = Application.objects.create(
            applicant_type=Application.Type.STUDENT,
            email="student@example.com",
            program=self.program,
            status=Application.Status.SUBMITTED,
            data={
                "step5-student": {
                    "legal_first_name": "Jane",
                    "last_name": "Doe",
                },
                "step7-primaryparent": {
                    "first_name": "John",
                    "last_name": "Doe",
                },
                "step8-secondaryparent": {
                    "first_name": "Mary",
                    "last_name": "Doe",
                },
            },
        )
        url = reverse("apply_submitted", kwargs={"app_id": application.application_id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        self.assertContains(response, "Jane Doe")  # Student
        self.assertContains(response, "John Doe")  # Primary Parent
        self.assertContains(response, "Mary Doe")  # Secondary Parent

    def test_submitted_page_shows_mentor_name(self):
        application = Application.objects.create(
            applicant_type=Application.Type.MENTOR,
            email="mentor@example.com",
            program=self.program,
            status=Application.Status.SUBMITTED,
            data={
                "mentor_info": {
                    "legal_first_name": "James",
                    "last_name": "Smith",
                }
            },
        )
        url = reverse("apply_submitted", kwargs={"app_id": application.application_id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "James Smith")

    def test_submitted_page_hides_skipped_secondary_parent(self):
        application = Application.objects.create(
            applicant_type=Application.Type.STUDENT,
            email="student@example.com",
            program=self.program,
            status=Application.Status.SUBMITTED,
            data={
                "step5-student": {"legal_first_name": "Jane", "last_name": "Doe"},
                "step7-primaryparent": {"first_name": "John", "last_name": "Doe"},
                "step8-secondaryparent": {"_skipped": True},
            },
        )
        url = reverse("apply_submitted", kwargs={"app_id": application.application_id})
        response = self.client.get(url)
        self.assertNotContains(response, "Secondary parent")


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class EmailSenderNameReproductionTest(TestCase):
    def test_default_from_email_with_name(self):
        app = Application.objects.create(email="test@example.com")
        with override_settings(
            DEFAULT_FROM_EMAIL="noreply@girlsofsteelrobotics.org",
            DEFAULT_FROM_NAME="Girls of Steel Admin",
        ):
            send_otp_email(app, "123456")

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(
            mail.outbox[0].from_email,
            '"Girls of Steel Admin" <noreply@girlsofsteelrobotics.org>',
        )


class GraduationYearValidationReproductionTest(TestCase):
    def test_graduation_year_cannot_be_in_past(self):
        current_year = timezone.now().year
        past_year = current_year - 1

        data = {
            "legal_first_name": "Jane",
            "last_name": "Doe",
            "graduation_year": past_year,
        }
        form = StudentInfoForm(data=data)
        self.assertIn("graduation_year", form.errors)
        self.assertEqual(
            form.errors["graduation_year"],
            [f"Ensure this value is greater than or equal to {current_year}."],
        )

    def test_graduation_year_can_be_current_or_future(self):
        current_year = timezone.now().year
        for year in [current_year, current_year + 1]:
            data = {
                "legal_first_name": "Jane",
                "last_name": "Doe",
                "graduation_year": year,
            }
            form = StudentInfoForm(data=data)
            # We check for graduation_year errors specifically
            self.assertNotIn("graduation_year", form.errors)


class WizardBackNavigationReproductionTests(TestCase):
    def setUp(self):
        self.program = Program.objects.create(
            name="Spring 2030",
            start_date=timezone.localdate() + datetime.timedelta(days=60),
            active=True,
        )

    def test_back_button_from_step3_leads_to_step2_if_verified(self):
        # 1. Create an application that has verified email
        app = Application.objects.create(
            email="test@example.com",
            applicant_type="student",
            email_verified_at=timezone.now(),
            current_step=4,  # Beyond step 3
        )

        # 2. Check Step 3 "Back" button (Email verification - redirects to Step 4)
        # But wait, Step 3 GET redirects to Step 4 if verified.
        # So testing Step 3's back button while verified is tricky via GET.
        # Let's check Step 4 "Back" button instead.
        response = self.client.get(
            reverse("apply_step4", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(response.status_code, 200)
        # In step4_program.html, Back button goes to Step 2 if verified.
        self.assertContains(
            response, reverse("apply_step2", kwargs={"app_id": app.application_id})
        )

    def test_accessing_step3_while_verified_redirects_forward(self):
        app = Application.objects.create(
            email="test@example.com",
            applicant_type="student",
            email_verified_at=timezone.now(),
            current_step=4,
        )
        # 3. Access Step 3 while verified
        response = self.client.get(
            reverse("apply_step3", kwargs={"app_id": app.application_id})
        )
        # It should redirect to Step 4
        self.assertRedirects(
            response, reverse("apply_step4", kwargs={"app_id": app.application_id})
        )

    def test_back_button_from_step5_leads_to_step4(self):
        # 1. Create an application that has verified email and chosen a program
        app = Application.objects.create(
            email="test@example.com",
            applicant_type="student",
            email_verified_at=timezone.now(),
            program=self.program,
            current_step=5,
        )

        # 2. Check Step 5 "Back" button (Student info)
        response = self.client.get(
            reverse("apply_step5", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(response.status_code, 200)
        # It should link to Step 4
        self.assertContains(
            response, reverse("apply_step4", kwargs={"app_id": app.application_id})
        )
        self.assertNotContains(
            response, reverse("apply_step3", kwargs={"app_id": app.application_id})
        )

    def test_back_button_from_mentor_info_leads_to_step2_if_verified(self):
        # 1. Create a mentor application that has verified email
        app = Application.objects.create(
            email="mentor@example.com",
            applicant_type="mentor",
            email_verified_at=timezone.now(),
            current_step=5,
        )

        # 2. Check Mentor Info "Back" button
        response = self.client.get(
            reverse("apply_mentor_info", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(response.status_code, 200)
        # It should link to Step 2 now, because email is already verified
        self.assertContains(
            response, reverse("apply_step2", kwargs={"app_id": app.application_id})
        )
        self.assertNotContains(
            response, reverse("apply_step4", kwargs={"app_id": app.application_id})
        )


class Step2LabelReproductionTest(TestCase):
    def test_step2_labels_not_duplicated(self):
        app = Application.objects.create(email="test@example.com")
        url = reverse("apply_step2", kwargs={"app_id": app.application_id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        content = response.content.decode()

        # Check for duplicated labels.
        # We expect "Student" to appear once in a label, not twice.
        # The duplicated labels might have slight differences in whitespace or attributes,
        # but both should be visible to the user.

        # radio_option.html has:
        # <label{% if widget.attrs.id %} for="{{ widget.attrs.id }}"{% endif %} class="form-check-label">
        #   {{ widget.label }}
        # </label>

        # step2_applicant_type.html has:
        # <label class="form-check-label" for="{{ choice.id_for_label }}">
        #     {{ choice.choice_label }}
        # </label>

        # Count occurrences of label text "Student" within label tags.
        student_labels = re.findall(r"<label[^>]*>\s*Student\s*</label>", content)
        self.assertEqual(
            len(student_labels),
            1,
            f"Expected 1 'Student' label, found {len(student_labels)}: {student_labels}",
        )

        parent_labels = re.findall(
            r"<label[^>]*>\s*Parent / Guardian\s*</label>", content
        )
        self.assertEqual(
            len(parent_labels),
            1,
            f"Expected 1 'Parent / Guardian' label, found {len(parent_labels)}: {parent_labels}",
        )

        mentor_labels = re.findall(
            r"<label[^>]*>\s*Mentor / Volunteer\s*</label>", content
        )
        # Mentor option is temporarily disabled
        self.assertEqual(
            len(mentor_labels),
            0,
            f"Expected 0 'Mentor / Volunteer' label (disabled), found {len(mentor_labels)}: {mentor_labels}",
        )

    def test_step4_labels_not_duplicated(self):
        Program.objects.create(
            name="Test Program",
            active=True,
            start_date=timezone.now().date() + datetime.timedelta(days=30),
            # applications are open starting today so it shows up in the future list
            applications_open=timezone.now().date(),
            applications_close=timezone.now().date() + datetime.timedelta(days=60),
        )
        app = Application.objects.create(
            email="test@example.com",
            email_verified_at=timezone.now(),
        )
        url = reverse("apply_step4", kwargs={"app_id": app.application_id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        content = response.content.decode()

        # Count occurrences of label text "Test Program" within label tags.
        # UI change: each radio option's <label> also wraps the <input>, so the
        # label contains additional markup. Match labels that CONTAIN the text
        # rather than matching text-only labels.
        program_labels = re.findall(
            r"<label[^>]*>[\s\S]*?Test Program( \(\d{4}\))?[\s\S]*?</label>",
            content,
        )
        # It should appear in the widget label.
        # Note: there is also an <h3>Test Program</h3> in the metadata section,
        # but we are only counting <label> tags.
        self.assertEqual(
            len(program_labels),
            1,
            f"Expected 1 'Test Program' label, found {len(program_labels)}: {program_labels}",
        )


class EmailSubaddressingValidationReproductionTests(TestCase):
    """
    Test that we correctly allow/prevent student emails being reused as parent emails.
    Subaddressing (+tag) is no longer stripped — only exact (case-insensitive) matches
    are blocked.
    """

    def test_parent_info_form_validation(self):
        from applications.forms import ParentInfoForm

        # Case 1: Exact same email (should be blocked)
        form = ParentInfoForm(
            data={"email": "name@email.com"}, student_emails=["name@email.com"]
        )
        self.assertFalse(form.is_valid())
        self.assertIn("email", form.errors)

        # Case 2: Parent uses +tag variant of student email (now ALLOWED —
        # we no longer strip subaddressing, so these are different addresses).
        form = ParentInfoForm(
            data={
                "first_name": "Pat",
                "last_name": "Parent",
                "relationship_to_student": "parent",
                "email": "name+parent@email.com",
                "address": "123 Main St",
                "city": "Pittsburgh",
                "state": "PA",
                "zip_code": "15213",
                "cell_phone": "555-444-1212",
            },
            student_emails=["name@email.com"],
        )
        self.assertTrue(form.is_valid(), form.errors)

        # Case 3: Tagged student email, raw parent email (should be allowed)
        form = ParentInfoForm(
            data={
                "first_name": "Pat",
                "last_name": "Parent",
                "relationship_to_student": "parent",
                "email": "name@email.com",
                "address": "123 Main St",
                "city": "Pittsburgh",
                "state": "PA",
                "zip_code": "15213",
                "cell_phone": "555-444-1212",
            },
            student_emails=["name+student@email.com"],
        )
        self.assertTrue(form.is_valid(), form.errors)

        # Case 4: Different bases, student has subaddressing (should be allowed)
        form = ParentInfoForm(
            data={
                "first_name": "Pat",
                "last_name": "Parent",
                "relationship_to_student": "parent",
                "email": "parent@email.com",
                "address": "123 Main St",
                "city": "Pittsburgh",
                "state": "PA",
                "zip_code": "15213",
                "cell_phone": "555-444-1212",
            },
            student_emails=["student+something@email.com"],
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_parent_handoff_form_validation(self):
        from applications.forms import ParentHandoffForm

        # Case: student+something@email.com and parent@email.com (should be allowed)
        form = ParentHandoffForm(
            data={"parent_email": "parent@email.com"},
            student_emails=["student+something@email.com"],
        )
        self.assertTrue(form.is_valid(), form.errors)

        # Case: student@email.com and name+parent@email.com (now ALLOWED —
        # subaddressing is no longer stripped, so these are different addresses).
        form = ParentHandoffForm(
            data={"parent_email": "name+parent@email.com"},
            student_emails=["name@email.com"],
        )
        self.assertTrue(form.is_valid(), form.errors)


class HandoffSecurityReproductionTests(TestCase):
    def test_student_cannot_bypass_handoff_by_resuming(self):
        # 1. Create a student-initiated application
        from django.utils import timezone

        app = Application.objects.create(
            applicant_type=Application.Type.STUDENT,
            email="student@example.com",
            email_verified_at=timezone.now(),
            current_step=6,
        )

        # 2. Advance to step 7 (handoff)
        response = self.client.get(
            reverse("apply_step7", kwargs={"app_id": app.application_id})
        )
        self.assertContains(
            response, "Now an adult contact needs to finish the application"
        )

        # 3. Perform handoff to parent
        response = self.client.post(
            reverse("apply_step7", kwargs={"app_id": app.application_id}),
            {"parent_email": "parent@example.com"},
        )

        app.refresh_from_db()
        self.assertEqual(app.status, Application.Status.AWAITING_PARENT)
        self.assertTrue("step7_handoff" in app.data)
        self.assertRedirects(response, reverse("apply_start"))

        # 4. Try to resume from home page (ResumeView)
        response = self.client.post(
            reverse("apply_resume"), {"application_id": app.application_id}
        )

        # It redirects to Step 7 because current_step is 7
        self.assertRedirects(
            response, reverse("apply_step7", kwargs={"app_id": app.application_id})
        )

        # Follow the redirect
        response = self.client.get(response.url)

        # DESIRED BEHAVIOR: it should show the handoff required page
        self.assertContains(
            response, "This application has been handed off to an adult contact"
        )
        self.assertNotContains(response, "Please provide the primary adult contact")

    def test_parent_can_access_handoff_with_token(self):
        # 1. Create a student-initiated application and hand it off
        from django.utils import timezone

        app = Application.objects.create(
            applicant_type=Application.Type.STUDENT,
            email="student@example.com",
            email_verified_at=timezone.now(),
            current_step=6,
        )
        self.client.post(
            reverse("apply_step7", kwargs={"app_id": app.application_id}),
            {"parent_email": "parent@example.com"},
        )
        app.refresh_from_db()
        token = app.handoff_token
        self.assertTrue(token)

        # 2. Access via ResumeLinkView with token
        response = self.client.get(
            reverse(
                "apply_resume_link_with_token",
                kwargs={"app_id": app.application_id, "token": token},
            )
        )

        # Should redirect to Step 7
        self.assertRedirects(
            response, reverse("apply_step7", kwargs={"app_id": app.application_id})
        )

        # Follow the redirect - should NOW be authorized because token is in session
        response = self.client.get(response.url)
        self.assertContains(response, "Please provide the primary adult contact")


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class ParentNotificationOptInReproductionTests(TestCase):
    def setUp(self):
        self.program = Program.objects.create(
            name="Spring 2030",
            start_date=timezone.localdate() + datetime.timedelta(days=60),
            active=True,
        )

    def test_primary_parent_optin_defaults_to_true(self):
        app = Application.objects.create(
            applicant_type=Application.Type.PARENT,
            email="parent@example.com",
            current_step=7,
            email_verified_at=timezone.now(),
            status=Application.Status.EMAIL_VERIFIED,
        )
        response = self.client.get(
            reverse("apply_step7", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(response.status_code, 200)

        # The checkbox for email_updates should be checked by default for primary parent
        self.assertContains(
            response,
            'id="id_email_updates" checked',
            html=False,
        )

    def test_secondary_parent_optin_defaults_to_false(self):
        app = Application.objects.create(
            applicant_type=Application.Type.PARENT,
            email="parent@example.com",
            current_step=8,
            email_verified_at=timezone.now(),
            status=Application.Status.EMAIL_VERIFIED,
        )
        response = self.client.get(
            reverse("apply_step8", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(response.status_code, 200)
        # The checkbox for email_updates should NOT be checked by default for secondary parent
        self.assertContains(
            response,
            'id="id_email_updates"',
            html=False,
        )
        self.assertNotContains(
            response,
            'id="id_email_updates" checked',
            html=False,
        )

    def test_both_parents_opt_out_blocked_at_step8(self):
        """If both parents opt out, the error should appear at step 8 (not step 9)."""
        app = Application.objects.create(
            applicant_type=Application.Type.PARENT,
            email="parent@example.com",
            current_step=8,
            email_verified_at=timezone.now(),
            status=Application.Status.EMAIL_VERIFIED,
            program=self.program,
            data={
                "step5-student": {"legal_first_name": "Grace", "last_name": "Hopper"},
                "step7-primaryparent": {
                    "first_name": "Pat",
                    "last_name": "Parent",
                    "email": "parent@example.com",
                    "email_updates": False,  # Primary parent opted out
                },
            },
        )
        response = self.client.post(
            reverse("apply_step8", kwargs={"app_id": app.application_id}),
            {
                "first_name": "Sam",
                "last_name": "Spouse",
                "relationship_to_student": "guardian",
                "address": "123 Main St",
                "city": "Pittsburgh",
                "state": "PA",
                "zip_code": "15213",
                "cell_phone": "412-555-0100",
                # email_updates not checked → False
            },
        )
        # Should stay on step 8 and show error
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "At least one adult contact must opt in to receiving email updates",
        )
        app.refresh_from_db()
        self.assertEqual(app.current_step, 8)

    def test_step8_proceeds_when_primary_parent_opted_in(self):
        """If primary parent opted in, step 8 should proceed even if secondary opts out."""
        app = Application.objects.create(
            applicant_type=Application.Type.PARENT,
            email="parent@example.com",
            current_step=8,
            email_verified_at=timezone.now(),
            status=Application.Status.EMAIL_VERIFIED,
            program=self.program,
            data={
                "step5-student": {"legal_first_name": "Grace", "last_name": "Hopper"},
                "step7-primaryparent": {
                    "first_name": "Pat",
                    "last_name": "Parent",
                    "email": "parent@example.com",
                    "email_updates": True,  # Primary parent opted in
                },
            },
        )
        response = self.client.post(
            reverse("apply_step8", kwargs={"app_id": app.application_id}),
            {
                "first_name": "Sam",
                "last_name": "Spouse",
                "relationship_to_student": "guardian",
                "address": "123 Main St",
                "city": "Pittsburgh",
                "state": "PA",
                "zip_code": "15213",
                "cell_phone": "412-555-0100",
                # email_updates not checked → False
            },
        )
        # Should proceed to step 9
        self.assertRedirects(
            response,
            reverse("apply_step9", kwargs={"app_id": app.application_id}),
        )

    def test_cannot_submit_without_at_least_one_parent_opting_in(self):
        # Create an application where both parents opted out in their respective steps
        app = Application.objects.create(
            applicant_type=Application.Type.PARENT,
            email="parent@example.com",
            current_step=9,
            email_verified_at=timezone.now(),
            status=Application.Status.EMAIL_VERIFIED,
            program=self.program,
            data={
                "step5-student": {"legal_first_name": "Grace", "last_name": "Hopper"},
                "step7-primaryparent": {
                    "first_name": "Pat",
                    "last_name": "Parent",
                    "email": "parent@example.com",
                    "email_updates": False,  # Opted out
                },
                "step8-secondaryparent": {
                    "first_name": "Sam",
                    "last_name": "Spouse",
                    "relationship_to_student": "guardian",
                    "email_updates": False,  # Opted out
                },
            },
        )
        response = self.client.post(
            reverse("apply_step9", kwargs={"app_id": app.application_id}),
            {"confirm": "on"},
        )
        # Should stay on page and show error
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "At least one parent or guardian must opt in to receiving email updates",
        )

        app.refresh_from_db()
        self.assertNotEqual(app.status, Application.Status.SUBMITTED)


class Step7PrefillEmailReproductionTests(TestCase):
    def test_parent_initiated_prefills_email(self):
        """
        If a parent starts the application, their verified email should
        prefill the Step 7 (Primary Parent) form by default.
        """
        app = Application.objects.create(
            applicant_type=Application.Type.PARENT,
            email="parent@example.com",
            email_verified_at=timezone.now(),
            current_step=4,
        )
        url = reverse("apply_step7", kwargs={"app_id": app.application_id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["form"].initial.get("email"), "parent@example.com"
        )

    def test_student_initiated_handoff_prefills_email(self):
        """
        If a student starts the application and hands off to a parent,
        the parent email provided during handoff should prefill Step 7.
        """
        app = Application.objects.create(
            applicant_type=Application.Type.STUDENT,
            email="student@example.com",
            email_verified_at=timezone.now(),
            current_step=7,
            data={"step7_handoff": {"parent_email": "parent_handoff@example.com"}},
        )
        # Mock session handoff token
        app.issue_handoff_token()
        app.status = Application.Status.AWAITING_PARENT
        app.save()

        session = self.client.session
        session[f"handoff_token_{app.application_id}"] = app.handoff_token
        session.save()

        url = reverse("apply_step7", kwargs={"app_id": app.application_id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["form"].initial.get("email"), "parent_handoff@example.com"
        )


class BooleanRenderingReproductionTest(TestCase):
    def setUp(self):
        self.reviewer = _reviewer_user()
        self.client.force_login(self.reviewer)

    def test_boolean_fields_displayed_as_yes_no(self):
        app = Application.objects.create(
            email="test@example.com",
            data={
                "step1": {
                    "some_checkbox": False,
                    "other_checkbox": True,
                },
            },
        )
        url = reverse(
            "application_review_detail", kwargs={"app_id": app.application_id}
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        self.assertContains(response, "Some checkbox")
        # Currently, False renders as "—"
        self.assertContains(response, "No")
        self.assertContains(response, "Other checkbox")
        self.assertContains(response, "Yes")
