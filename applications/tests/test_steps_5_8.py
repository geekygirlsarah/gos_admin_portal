"""Tests for the application wizard Steps 5-8 (Phase 2)."""

from __future__ import annotations

import datetime

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from applications.models import Application
from programs.models import Adult, Program, Student


def _verified(**kwargs):
    """Convenience: create an application that has cleared Steps 1-4."""
    defaults = dict(
        applicant_type=Application.Type.PARENT,
        email="parent@example.com",
        current_step=5,
        email_verified_at=timezone.now(),
        status=Application.Status.EMAIL_VERIFIED,
    )
    defaults.update(kwargs)
    return Application.objects.create(**defaults)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class Step5StudentInfoTests(TestCase):
    def setUp(self):
        today = timezone.localdate()
        self.program = Program.objects.create(
            name="Spring 2030",
            year=2030,
            start_date=today + datetime.timedelta(days=60),
            end_date=today + datetime.timedelta(days=120),
            active=True,
        )
        mail.outbox = []

    def test_step5_redirects_to_step4_if_email_not_verified(self):
        app = Application.objects.create(
            applicant_type=Application.Type.PARENT,
            email="parent@example.com",
            current_step=5,
        )
        response = self.client.get(
            reverse("apply_step5", kwargs={"app_id": app.application_id})
        )
        self.assertRedirects(
            response,
            reverse("apply_step4", kwargs={"app_id": app.application_id}),
            fetch_redirect_response=False,
        )

    def test_step5_get_prefills_for_existing_student_email(self):
        Student.objects.create(
            legal_first_name="Ada",
            last_name="Lovelace",
            personal_email="ada@example.com",
        )
        app = _verified(
            applicant_type=Application.Type.STUDENT, email="ada@example.com"
        )
        response = self.client.get(
            reverse("apply_step5", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="Ada"')
        self.assertContains(response, 'value="Lovelace"')

    def test_step5_post_saves_data_and_advances_to_step6(self):
        app = _verified()
        response = self.client.post(
            reverse("apply_step5", kwargs={"app_id": app.application_id}),
            {
                "legal_first_name": "Grace",
                "first_name": "",
                "last_name": "Hopper",
                "pronouns": "",
                "personal_email": "grace@example.com",
                "cell_phone_number": "",
                "school_name": "",
                "graduation_year": "",
                "tshirt_size": "",
                "allergies": "",
                "dietary_restrictions": "",
                "medical_notes": "",
            },
        )
        self.assertRedirects(
            response,
            reverse("apply_step6", kwargs={"app_id": app.application_id}),
            fetch_redirect_response=False,
        )
        app.refresh_from_db()
        self.assertEqual(app.data.get("step5", {}).get("legal_first_name"), "Grace")
        self.assertGreaterEqual(app.current_step, 6)

    def test_step5_picker_shown_for_parent_with_existing_children(self):
        adult = Adult.objects.create(
            first_name="Pat",
            last_name="Parent",
            email="parent@example.com",
            is_parent=True,
        )
        student_a = Student.objects.create(
            legal_first_name="Anna",
            last_name="Smith",
            primary_contact=adult,
        )
        Student.objects.create(
            legal_first_name="Bea",
            last_name="Smith",
            primary_contact=adult,
        )
        app = _verified(email="parent@example.com")
        response = self.client.get(
            reverse("apply_step5", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Anna")
        self.assertContains(response, "Bea")
        # Selecting one prefills the form fields.
        response2 = self.client.post(
            reverse("apply_step5", kwargs={"app_id": app.application_id}),
            {"student": str(student_a.pk), "_pick_student": "1"},
        )
        self.assertEqual(response2.status_code, 200)
        self.assertContains(response2, 'value="Anna"')


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class Step6PrimaryParentTests(TestCase):
    def setUp(self):
        mail.outbox = []

    def test_student_initiated_no_existing_parent_shows_handoff(self):
        app = _verified(
            applicant_type=Application.Type.STUDENT, email="kid@example.com"
        )
        response = self.client.get(
            reverse("apply_step6", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Email my parent")

    def test_student_handoff_post_emails_parent_and_redirects_to_start(self):
        app = _verified(
            applicant_type=Application.Type.STUDENT, email="kid@example.com"
        )
        response = self.client.post(
            reverse("apply_step6", kwargs={"app_id": app.application_id}),
            {"parent_email": "guardian@example.com"},
        )
        self.assertRedirects(
            response, reverse("apply_start"), fetch_redirect_response=False
        )
        app.refresh_from_db()
        self.assertEqual(app.status, Application.Status.AWAITING_PARENT)
        self.assertEqual(
            app.data.get("step6_handoff", {}).get("parent_email"),
            "guardian@example.com",
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("guardian@example.com", mail.outbox[0].to)
        self.assertIn(app.application_id, mail.outbox[0].body)

    def test_parent_with_existing_adult_record_prefills_form(self):
        Adult.objects.create(
            first_name="Pat",
            last_name="Parent",
            email="parent@example.com",
            cell_phone="555-1212",
            is_parent=True,
        )
        app = _verified(email="parent@example.com")
        response = self.client.get(
            reverse("apply_step6", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="Pat"')
        self.assertContains(response, 'value="Parent"')
        self.assertContains(response, "555-1212")

    def test_parent_form_post_saves_and_advances_to_step7(self):
        app = _verified()
        response = self.client.post(
            reverse("apply_step6", kwargs={"app_id": app.application_id}),
            {
                "first_name": "Pat",
                "last_name": "Parent",
                "relationship_to_student": "parent",
                "email": "parent@example.com",
                "cell_phone": "",
                "home_phone": "",
            },
        )
        self.assertRedirects(
            response,
            reverse("apply_step7", kwargs={"app_id": app.application_id}),
            fetch_redirect_response=False,
        )
        app.refresh_from_db()
        self.assertEqual(app.data.get("step6", {}).get("first_name"), "Pat")
        self.assertGreaterEqual(app.current_step, 7)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class Step7SecondaryParentTests(TestCase):
    def test_secondary_parent_is_required(self):
        # Posting an empty form (or the legacy "skip" button) must not
        # advance to step 8 — a secondary contact is required.
        app = _verified(current_step=7)
        response = self.client.post(
            reverse("apply_step7", kwargs={"app_id": app.application_id}),
            {"skip": "1"},
        )
        self.assertEqual(response.status_code, 200)
        app.refresh_from_db()
        self.assertNotIn("step7", app.data or {})

    def test_filled_form_saves_and_advances(self):
        app = _verified(current_step=7)
        response = self.client.post(
            reverse("apply_step7", kwargs={"app_id": app.application_id}),
            {
                "first_name": "Sam",
                "last_name": "Spouse",
                "relationship_to_student": "guardian",
                "email": "",
                "cell_phone": "",
                "home_phone": "",
            },
        )
        self.assertRedirects(
            response,
            reverse("apply_step8", kwargs={"app_id": app.application_id}),
            fetch_redirect_response=False,
        )
        app.refresh_from_db()
        self.assertEqual(app.data.get("step7", {}).get("first_name"), "Sam")


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class Step8ConfirmTests(TestCase):
    def setUp(self):
        today = timezone.localdate()
        self.program = Program.objects.create(
            name="Spring 2030",
            year=2030,
            start_date=today + datetime.timedelta(days=60),
            end_date=today + datetime.timedelta(days=120),
            active=True,
        )
        mail.outbox = []

    def _verified_with_data(self):
        return _verified(
            program=self.program,
            current_step=8,
            data={
                "step5": {"legal_first_name": "Grace", "last_name": "Hopper"},
                "step6": {
                    "first_name": "Pat",
                    "last_name": "Parent",
                    "email": "parent@example.com",
                },
                "step7": {
                    "first_name": "Sam",
                    "last_name": "Spouse",
                    "relationship_to_student": "guardian",
                },
            },
        )

    def test_get_renders_review_with_collected_data(self):
        app = self._verified_with_data()
        response = self.client.get(
            reverse("apply_step8", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Grace")
        self.assertContains(response, "Pat")
        self.assertContains(response, self.program.name)

    def test_post_without_confirm_stays_on_page(self):
        app = self._verified_with_data()
        response = self.client.post(
            reverse("apply_step8", kwargs={"app_id": app.application_id}),
            {},
        )
        self.assertEqual(response.status_code, 200)
        app.refresh_from_db()
        self.assertNotEqual(app.status, Application.Status.SUBMITTED)

    def test_post_confirms_and_submits_application(self):
        app = self._verified_with_data()
        response = self.client.post(
            reverse("apply_step8", kwargs={"app_id": app.application_id}),
            {"confirm": "on"},
        )
        self.assertRedirects(
            response,
            reverse("apply_submitted", kwargs={"app_id": app.application_id}),
            fetch_redirect_response=False,
        )
        app.refresh_from_db()
        self.assertEqual(app.status, Application.Status.SUBMITTED)
        self.assertIsNotNone(app.submitted_at)
        # Two emails: confirmation to applicant + lead-mentor notification.
        self.assertEqual(len(mail.outbox), 2)
        recipients = {addr for m in mail.outbox for addr in m.to}
        self.assertIn(app.email, recipients)
        # Confirmation should also go to the primary parent's email from
        # Step 6 (in addition to the student/applicant email).
        self.assertIn("parent@example.com", recipients)
        # Default lead recipient
        self.assertTrue(
            any("leads@girlsofsteelrobotics.org" in addr for addr in recipients)
        )
        # Find the applicant confirmation email specifically and assert its
        # recipient list contains both the applicant and the parent.
        confirm_msgs = [
            m for m in mail.outbox if "leads@girlsofsteelrobotics.org" not in m.to
        ]
        self.assertEqual(len(confirm_msgs), 1)
        confirm = confirm_msgs[0]
        self.assertIn(app.email, confirm.to)
        self.assertIn("parent@example.com", confirm.to)

    def test_submit_sends_only_parent_when_student_has_no_email(self):
        # Simulate a student-without-email flow where Step 2 captured the
        # parent's email as application.email (no separate step5.email),
        # and Step 6 records the same parent email. The confirmation should
        # be sent once, to that single address.
        app = _verified(
            program=self.program,
            current_step=8,
            email="parent@example.com",
            data={
                "step5": {"legal_first_name": "Ada", "last_name": "Lovelace"},
                "step6": {
                    "first_name": "Pat",
                    "last_name": "Parent",
                    "email": "parent@example.com",
                },
                "step7": {
                    "first_name": "Sam",
                    "last_name": "Spouse",
                    "relationship_to_student": "guardian",
                },
            },
        )
        self.client.post(
            reverse("apply_step8", kwargs={"app_id": app.application_id}),
            {"confirm": "on"},
        )
        confirm_msgs = [
            m for m in mail.outbox if "leads@girlsofsteelrobotics.org" not in m.to
        ]
        self.assertEqual(len(confirm_msgs), 1)
        self.assertEqual(confirm_msgs[0].to, ["parent@example.com"])

    def test_submitted_page_renders(self):
        app = self._verified_with_data()
        app.status = Application.Status.SUBMITTED
        app.submitted_at = timezone.now()
        app.current_step = 9
        app.save()
        response = self.client.get(
            reverse("apply_submitted", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, app.application_id)


class SwapParentsViewTests(TestCase):
    """Tests for the swap-parents endpoint."""

    def _app_with_both_parents(self, **kwargs):
        defaults = dict(
            applicant_type=Application.Type.PARENT,
            email="parent@example.com",
            current_step=7,
            email_verified_at=timezone.now(),
            status=Application.Status.EMAIL_VERIFIED,
            data={
                "step6": {
                    "first_name": "Joe",
                    "last_name": "Primary",
                    "email": "joe@example.com",
                },
                "step7": {
                    "first_name": "Jane",
                    "last_name": "Secondary",
                },
            },
        )
        defaults.update(kwargs)
        return Application.objects.create(**defaults)

    def test_swap_exchanges_step6_and_step7_data(self):
        app = self._app_with_both_parents()
        self.client.post(
            reverse("apply_swap_parents", kwargs={"app_id": app.application_id}),
            {"next": "6"},
        )
        app.refresh_from_db()
        self.assertEqual(app.data["step6"]["first_name"], "Jane")
        self.assertEqual(app.data["step7"]["first_name"], "Joe")

    def test_swap_redirects_to_step6_by_default(self):
        app = self._app_with_both_parents()
        response = self.client.post(
            reverse("apply_swap_parents", kwargs={"app_id": app.application_id}),
            {"next": "6"},
        )
        self.assertRedirects(
            response,
            reverse("apply_step6", kwargs={"app_id": app.application_id}),
            fetch_redirect_response=False,
        )

    def test_swap_redirects_to_step7_when_requested(self):
        app = self._app_with_both_parents()
        response = self.client.post(
            reverse("apply_swap_parents", kwargs={"app_id": app.application_id}),
            {"next": "7"},
        )
        self.assertRedirects(
            response,
            reverse("apply_step7", kwargs={"app_id": app.application_id}),
            fetch_redirect_response=False,
        )

    def test_swap_twice_restores_original_data(self):
        app = self._app_with_both_parents()
        url = reverse("apply_swap_parents", kwargs={"app_id": app.application_id})
        self.client.post(url, {"next": "6"})
        self.client.post(url, {"next": "6"})
        app.refresh_from_db()
        self.assertEqual(app.data["step6"]["first_name"], "Joe")
        self.assertEqual(app.data["step7"]["first_name"], "Jane")

    def test_swap_requires_verified_email(self):
        app = Application.objects.create(
            applicant_type=Application.Type.PARENT,
            email="parent@example.com",
            current_step=6,
            data={
                "step6": {"first_name": "Joe"},
                "step7": {"first_name": "Jane"},
            },
        )
        response = self.client.post(
            reverse("apply_swap_parents", kwargs={"app_id": app.application_id}),
            {"next": "6"},
        )
        # Should redirect to email verification, not swap.
        self.assertRedirects(
            response,
            reverse("apply_step4", kwargs={"app_id": app.application_id}),
            fetch_redirect_response=False,
        )
        app.refresh_from_db()
        # Data must be unchanged.
        self.assertEqual(app.data["step6"]["first_name"], "Joe")


class ResumeRedirectsToCurrentStepTests(TestCase):
    """Resume should land users on the right step 5/6/7/8."""

    def test_resume_to_step5(self):
        app = _verified(current_step=5)
        response = self.client.post(
            reverse("apply_resume"),
            {"application_id": app.application_id},
        )
        self.assertRedirects(
            response,
            reverse("apply_step5", kwargs={"app_id": app.application_id}),
            fetch_redirect_response=False,
        )

    def test_resume_to_step8(self):
        app = _verified(current_step=8)
        response = self.client.post(
            reverse("apply_resume"),
            {"application_id": app.application_id},
        )
        self.assertRedirects(
            response,
            reverse("apply_step8", kwargs={"app_id": app.application_id}),
            fetch_redirect_response=False,
        )
