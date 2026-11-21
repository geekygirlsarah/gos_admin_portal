"""Tests for the mentor application wizard branch."""

from __future__ import annotations

import re
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase
from django.urls import reverse

from applications.forms import ConfirmSubmitForm, MentorClearanceDetailForm
from applications.models import Application
from organizations.models import Organization, UserOrganizationMembership
from programs.models import Adult


def _make_verified_mentor_app(email: str = "newmentor@example.com") -> Application:
    """Helper: an Application with applicant_type=MENTOR and verified email."""
    from django.utils import timezone

    organization = Organization.objects.get(slug="gos")
    app = Application.objects.create(
        organization=organization,
        applicant_type=Application.Type.MENTOR,
        email=email,
        email_verified_at=timezone.now(),
        current_step=5,
    )
    return app


class MentorFlowTests(TestCase):
    def setUp(self):
        mail.outbox = []

    def start_application(self):
        response = self.client.post(reverse("apply_start"))
        self.assertEqual(response.status_code, 302)
        app_id = response["Location"].rstrip("/").split("/")[-2]
        return Application.objects.get(application_id=app_id)

    def set_mentor_type_and_email(self, app, email):
        return self.client.post(
            reverse("apply_step2", kwargs={"app_id": app.application_id}),
            {"applicant_type": Application.Type.MENTOR, "email": email},
        )

    def verify_browser(self, app):
        url = reverse(
            "apply_browser_verification", kwargs={"app_id": app.application_id}
        )
        with (
            patch(
                "applications.views.browser.generate_otp_code", return_value="123456"
            ),
            patch("applications.views.browser.send_otp_email"),
        ):
            response = self.client.post(url, {"action": "send"})
        self.assertEqual(response.status_code, 302)
        response = self.client.post(url, {"code": "123456"})
        self.assertEqual(response.status_code, 302)

    def verified_mentor_app(self, email="newmentor@example.com"):
        app = _make_verified_mentor_app(email=email)
        self.verify_browser(app)
        return app

    def email_otp(self):
        return re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)

    # --- Step 2 → Step 3 (Email) for mentor -----------------------------

    def test_mentor_step2_goes_to_step3(self):
        app = self.start_application()
        response = self.set_mentor_type_and_email(app, "mentor@example.com")
        self.assertRedirects(
            response,
            reverse("apply_step3", kwargs={"app_id": app.application_id}),
            fetch_redirect_response=False,
        )
        app.refresh_from_db()
        self.assertEqual(app.applicant_type, "mentor")

    # --- Step 3 (OTP) → mentor-info or blocked --------------------------

    def test_mentor_otp_success_redirects_to_mentor_info_when_new(self):
        app = self.start_application()
        self.set_mentor_type_and_email(app, "fresh@example.com")
        code = self.email_otp()
        response = self.client.post(
            reverse("apply_step3", kwargs={"app_id": app.application_id}),
            {"code": code},
        )
        self.assertRedirects(
            response,
            reverse("apply_mentor_info", kwargs={"app_id": app.application_id}),
            fetch_redirect_response=False,
        )

    def test_mentor_step3_get_when_already_verified_redirects_to_mentor_flow(self):
        # Visiting the email-verify page again with a verified email must NOT
        # send a mentor to the student program-selection step.
        app = self.verified_mentor_app()
        response = self.client.get(
            reverse("apply_step3", kwargs={"app_id": app.application_id})
        )
        self.assertRedirects(
            response,
            reverse("apply_mentor_info", kwargs={"app_id": app.application_id}),
            fetch_redirect_response=False,
        )

    def test_mentor_going_back_to_step2_then_forward_stays_in_mentor_flow(self):
        # Regression: after getting partway through the mentor wizard, going
        # back to step 2 and pressing Continue again used to land the user in
        # the student program-selection step. It should return to the mentor
        # questions instead.
        app = self.verified_mentor_app()
        app.data = {"mentor_info": {"legal_first_name": "Alex", "last_name": "Lee"}}
        app.save()
        response = self.client.post(
            reverse("apply_step2", kwargs={"app_id": app.application_id}),
            {"applicant_type": "mentor", "email": "newmentor@example.com"},
        )
        self.assertRedirects(
            response,
            reverse("apply_step3", kwargs={"app_id": app.application_id}),
            fetch_redirect_response=False,
        )
        response = self.client.get(response["Location"])
        self.assertRedirects(
            response,
            reverse("apply_mentor_info", kwargs={"app_id": app.application_id}),
            fetch_redirect_response=False,
        )

    def test_mentor_otp_success_blocks_existing_mentor(self):
        Adult.objects.create(
            legal_first_name="Pat",
            last_name="Mentor",
            personal_email="onfile@example.com",
            is_mentor=True,
        )
        app = self.start_application()
        self.set_mentor_type_and_email(app, "onfile@example.com")
        code = self.email_otp()
        response = self.client.post(
            reverse("apply_step3", kwargs={"app_id": app.application_id}),
            {"code": code},
        )
        self.assertRedirects(
            response,
            reverse("apply_mentor_blocked", kwargs={"app_id": app.application_id}),
            fetch_redirect_response=False,
        )

    def test_mentor_blocked_page_renders(self):
        Adult.objects.create(
            legal_first_name="Pat",
            last_name="Mentor",
            personal_email="onfile@example.com",
            is_mentor=True,
        )
        app = self.verified_mentor_app(email="onfile@example.com")
        response = self.client.get(
            reverse("apply_mentor_blocked", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already")

    # --- Mentor info ----------------------------------------------------

    def test_mentor_info_get_requires_verified_email(self):
        app = self.start_application()
        self.set_mentor_type_and_email(app, "x@example.com")
        response = self.client.get(
            reverse("apply_mentor_info", kwargs={"app_id": app.application_id})
        )
        self.assertRedirects(
            response,
            reverse(
                "apply_browser_verification", kwargs={"app_id": app.application_id}
            ),
            fetch_redirect_response=False,
        )
        self.verify_browser(app)
        response = self.client.get(
            reverse("apply_mentor_info", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(response.status_code, 200)
        app.refresh_from_db()
        self.assertTrue(app.email_is_verified)

    def test_mentor_info_get_blocks_non_mentor_applicants(self):
        app = Application.objects.create(
            applicant_type=Application.Type.STUDENT,
            email="kid@example.com",
        )
        from django.utils import timezone

        app.email_verified_at = timezone.now()
        app.save()
        self.verify_browser(app)
        response = self.client.get(
            reverse("apply_mentor_info", kwargs={"app_id": app.application_id})
        )
        # Non-mentor should be redirected away (to their own flow).
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("/mentor/", response["Location"])

    def test_mentor_info_post_saves_and_advances(self):
        app = self.verified_mentor_app()
        response = self.client.post(
            reverse("apply_mentor_info", kwargs={"app_id": app.application_id}),
            {
                "legal_first_name": "Alex",
                "preferred_first_name": "",
                "last_name": "Lee",
                "phone_number": "555-444-1212",
                "phone_type": "cell",
                "discord_username": "alexlee#1234",
                "andrew_id": "",
                "employer": "Acme Robotics",
                "notes": "Excited to help.",
            },
        )
        self.assertRedirects(
            response,
            reverse(
                "apply_mentor_clearance_interest",
                kwargs={"app_id": app.application_id},
            ),
            fetch_redirect_response=False,
        )
        app.refresh_from_db()
        self.assertEqual(app.data["mentor_info"]["legal_first_name"], "Alex")
        self.assertEqual(app.data["mentor_info"]["last_name"], "Lee")

    # --- Clearance interest & detail ------------------------------------

    def test_mentor_clearance_interest_no_skips_detail(self):
        app = self.verified_mentor_app()
        # First save mentor_info so we're allowed on subsequent steps.
        app.data = {"mentor_info": {"legal_first_name": "A", "last_name": "L"}}
        app.save()
        response = self.client.post(
            reverse(
                "apply_mentor_clearance_interest",
                kwargs={"app_id": app.application_id},
            ),
            {"interested": "no"},
        )
        self.assertRedirects(
            response,
            reverse("apply_mentor_confirm", kwargs={"app_id": app.application_id}),
            fetch_redirect_response=False,
        )
        app.refresh_from_db()
        self.assertEqual(app.data["mentor_clearance_interest"]["interested"], "no")
        self.assertNotIn("mentor_clearance_detail", app.data)

    def test_mentor_clearance_interest_yes_goes_to_detail(self):
        app = self.verified_mentor_app()
        app.data = {"mentor_info": {"legal_first_name": "A", "last_name": "L"}}
        app.save()
        response = self.client.post(
            reverse(
                "apply_mentor_clearance_interest",
                kwargs={"app_id": app.application_id},
            ),
            {"interested": "yes"},
        )
        self.assertRedirects(
            response,
            reverse(
                "apply_mentor_clearance_detail",
                kwargs={"app_id": app.application_id},
            ),
            fetch_redirect_response=False,
        )

    def test_mentor_clearance_detail_get_redirects_if_not_interested(self):
        app = self.verified_mentor_app()
        app.data = {"mentor_clearance_interest": {"interested": "no"}}
        app.save()
        response = self.client.get(
            reverse(
                "apply_mentor_clearance_detail",
                kwargs={"app_id": app.application_id},
            )
        )
        self.assertRedirects(
            response,
            reverse(
                "apply_mentor_clearance_interest",
                kwargs={"app_id": app.application_id},
            ),
            fetch_redirect_response=False,
        )

    def test_mentor_clearance_detail_post_saves_and_advances(self):
        app = self.verified_mentor_app()
        app.data = {"mentor_clearance_interest": {"interested": "yes"}}
        app.save()
        response = self.client.post(
            reverse(
                "apply_mentor_clearance_detail",
                kwargs={"app_id": app.application_id},
            ),
            {"paca": "have", "patch": "need", "fbi": "need"},
        )
        self.assertRedirects(
            response,
            reverse("apply_mentor_confirm", kwargs={"app_id": app.application_id}),
            fetch_redirect_response=False,
        )
        app.refresh_from_db()
        self.assertEqual(app.data["mentor_clearance_detail"]["paca"], "have")
        self.assertEqual(app.data["mentor_clearance_detail"]["fbi"], "need")

    def test_clearance_detail_write_rechecks_current_interest(self):
        app = self.verified_mentor_app()
        app.data = {"mentor_clearance_interest": {"interested": "yes"}}
        app.save()
        original_is_valid = MentorClearanceDetailForm.is_valid
        changed = False

        def is_valid(form):
            nonlocal changed
            valid = original_is_valid(form)
            if valid and not changed:
                changed = True
                latest = Application.objects.get(pk=app.pk)
                latest.data["mentor_clearance_interest"]["interested"] = "no"
                latest.save(update_fields=["data", "updated_at"])
            return valid

        with patch.object(MentorClearanceDetailForm, "is_valid", new=is_valid):
            response = self.client.post(
                reverse(
                    "apply_mentor_clearance_detail",
                    kwargs={"app_id": app.application_id},
                ),
                {"paca": "have", "patch": "need", "fbi": "need"},
            )

        self.assertRedirects(
            response,
            reverse(
                "apply_mentor_clearance_interest",
                kwargs={"app_id": app.application_id},
            ),
            fetch_redirect_response=False,
        )
        app.refresh_from_db()
        self.assertEqual(app.data["mentor_clearance_interest"]["interested"], "no")
        self.assertNotIn("mentor_clearance_detail", app.data)

    def test_mentor_step_write_does_not_update_application_submitted_mid_request(self):
        app = self.verified_mentor_app()
        app.data = {"mentor_clearance_interest": {"interested": "yes"}}
        app.save()
        original_is_valid = MentorClearanceDetailForm.is_valid

        def submit_latest_application(form):
            valid = original_is_valid(form)
            if valid:
                Application.objects.filter(pk=app.pk).update(
                    status=Application.Status.SUBMITTED
                )
            return valid

        with patch.object(
            MentorClearanceDetailForm, "is_valid", new=submit_latest_application
        ):
            response = self.client.post(
                reverse(
                    "apply_mentor_clearance_detail",
                    kwargs={"app_id": app.application_id},
                ),
                {"paca": "have", "patch": "need", "fbi": "need"},
            )

        self.assertRedirects(
            response,
            reverse("apply_submitted", kwargs={"app_id": app.application_id}),
            fetch_redirect_response=False,
        )
        app.refresh_from_db()
        self.assertEqual(app.status, Application.Status.SUBMITTED)
        self.assertNotIn("mentor_clearance_detail", app.data)

    # --- Confirm + submit ----------------------------------------------

    def test_mentor_confirm_submission_sets_status_and_emails(self):
        app = self.verified_mentor_app()
        lead = get_user_model().objects.create_user(
            username="mentor-flow-lead",
            email="mentor-flow-lead@example.test",
        )
        UserOrganizationMembership.objects.create(
            user=lead,
            organization=app.organization,
            role="LeadMentor",
            is_active=True,
        )
        app.data = {
            "mentor_info": {
                "legal_first_name": "Alex",
                "last_name": "Lee",
                "employer": "Acme",
            },
            "mentor_clearance_interest": {"interested": "no"},
        }
        app.save()
        response = self.client.post(
            reverse("apply_mentor_confirm", kwargs={"app_id": app.application_id}),
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
        # Confirmation to applicant + lead-mentor notification.
        recipients = [r for m in mail.outbox for r in m.to]
        self.assertIn("newmentor@example.com", recipients)
        # A lead mentor in this organization's active membership receives the notice.
        self.assertTrue(
            any("mentor-flow-lead@example.test" in m.to for m in mail.outbox)
        )
        sent_email_count = len(mail.outbox)
        submitted_at = app.submitted_at

        response = self.client.post(
            reverse("apply_mentor_confirm", kwargs={"app_id": app.application_id}),
            {"confirm": "on"},
        )

        self.assertRedirects(
            response,
            reverse("apply_submitted", kwargs={"app_id": app.application_id}),
            fetch_redirect_response=False,
        )
        app.refresh_from_db()
        self.assertEqual(app.submitted_at, submitted_at)
        self.assertEqual(len(mail.outbox), sent_email_count)

    def test_mentor_confirmation_rechecks_browser_identity_after_validation(self):
        app = self.verified_mentor_app()
        original_status = app.status
        mail.outbox = []
        original_is_valid = ConfirmSubmitForm.is_valid
        changed = False

        def is_valid(form):
            nonlocal changed
            valid = original_is_valid(form)
            if valid and not changed:
                changed = True
                Application.objects.filter(pk=app.pk).update(
                    email="current-mentor@example.test",
                    email_verified_at=None,
                )
            return valid

        with patch.object(ConfirmSubmitForm, "is_valid", new=is_valid):
            response = self.client.post(
                reverse("apply_mentor_confirm", kwargs={"app_id": app.application_id}),
                {"confirm": "on"},
            )

        self.assertRedirects(
            response,
            reverse(
                "apply_browser_verification", kwargs={"app_id": app.application_id}
            ),
            fetch_redirect_response=False,
        )
        app.refresh_from_db()
        self.assertEqual(app.email, "current-mentor@example.test")
        self.assertIsNone(app.email_verified_at)
        self.assertEqual(app.status, original_status)
        self.assertEqual(mail.outbox, [])

    def test_mentor_submitted_page_renders(self):
        app = self.verified_mentor_app()
        app.status = Application.Status.SUBMITTED
        app.save()
        response = self.client.get(
            reverse("apply_submitted", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, app.application_id)

    # --- Resume routing -------------------------------------------------

    def test_resume_link_routes_unverified_mentor_to_email_verify(self):
        # An unverified mentor resuming must go to the email-verify page
        # (apply_step3), never to the student program-selection page.
        app = Application.objects.create(
            applicant_type=Application.Type.MENTOR,
            email="x@example.com",
            current_step=3,
        )
        response = self.client.get(
            reverse("apply_resume_link", kwargs={"app_id": app.application_id})
        )
        self.assertRedirects(
            response,
            reverse("apply_step3", kwargs={"app_id": app.application_id}),
            fetch_redirect_response=False,
        )

    def test_continue_for_unverified_mentor_goes_to_email_verify(self):
        app = self.start_application()
        self.set_mentor_type_and_email(app, "x@example.com")
        app.current_step = 5
        app.save(update_fields=["current_step", "updated_at"])
        response = self.client.get(
            reverse("apply_continue", kwargs={"app_id": app.application_id})
        )
        self.assertRedirects(
            response,
            reverse("apply_step3", kwargs={"app_id": app.application_id}),
            fetch_redirect_response=False,
        )

    def test_resume_link_routes_mentor_to_mentor_info_after_otp(self):
        app = self.verified_mentor_app()
        # current_step=5 (past OTP) and no mentor_info yet.
        response = self.client.get(
            reverse("apply_resume_link", kwargs={"app_id": app.application_id})
        )
        self.assertRedirects(
            response,
            reverse("apply_mentor_info", kwargs={"app_id": app.application_id}),
            fetch_redirect_response=False,
        )

    def test_resume_link_routes_mentor_to_clearance_interest(self):
        app = self.verified_mentor_app()
        app.data = {"mentor_info": {"legal_first_name": "A", "last_name": "L"}}
        app.save()
        response = self.client.get(
            reverse("apply_resume_link", kwargs={"app_id": app.application_id})
        )
        self.assertRedirects(
            response,
            reverse(
                "apply_mentor_clearance_interest",
                kwargs={"app_id": app.application_id},
            ),
            fetch_redirect_response=False,
        )

    def test_resume_link_routes_submitted_mentor_to_submitted_page(self):
        app = self.verified_mentor_app()
        app.status = Application.Status.SUBMITTED
        app.save()
        response = self.client.get(
            reverse("apply_resume_link", kwargs={"app_id": app.application_id})
        )
        self.assertRedirects(
            response,
            reverse("apply_submitted", kwargs={"app_id": app.application_id}),
            fetch_redirect_response=False,
        )
