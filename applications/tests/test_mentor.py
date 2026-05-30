"""Tests for the mentor application wizard branch."""

from __future__ import annotations

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from applications.models import Application
from programs.models import Adult


def _make_verified_mentor_app(email: str = "newmentor@example.com") -> Application:
    """Helper: an Application with applicant_type=MENTOR and verified email."""
    from django.utils import timezone

    app = Application.objects.create(
        applicant_type=Application.Type.MENTOR,
        email=email,
        email_verified_at=timezone.now(),
        current_step=5,
    )
    return app


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class MentorFlowTests(TestCase):
    def setUp(self):
        mail.outbox = []

    # --- Step 2 → Step 3 (Email) for mentor -----------------------------

    def test_mentor_step2_fails_when_disabled(self):
        """Verify that Step 2 now rejects 'mentor' because it's disabled."""
        app = Application.objects.create()
        response = self.client.post(
            reverse("apply_step2", kwargs={"app_id": app.application_id}),
            {"applicant_type": "mentor", "email": "mentor@example.com"},
        )
        # 200 means form error (stay on page) instead of 302 redirect.
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Select a valid choice")

    # --- Step 3 (OTP) → mentor-info or blocked --------------------------

    def test_mentor_otp_success_redirects_to_mentor_info_when_new(self):
        app = Application.objects.create(
            applicant_type=Application.Type.MENTOR,
            email="fresh@example.com",
        )
        code = app.issue_otp()
        response = self.client.post(
            reverse("apply_step3", kwargs={"app_id": app.application_id}),
            {"code": code},
        )
        self.assertRedirects(
            response,
            reverse("apply_mentor_info", kwargs={"app_id": app.application_id}),
            fetch_redirect_response=False,
        )

    def test_mentor_otp_success_blocks_existing_mentor(self):
        Adult.objects.create(
            first_name="Pat",
            last_name="Mentor",
            email="onfile@example.com",
            is_mentor=True,
        )
        app = Application.objects.create(
            applicant_type=Application.Type.MENTOR,
            email="onfile@example.com",
        )
        code = app.issue_otp()
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
            first_name="Pat",
            last_name="Mentor",
            email="onfile@example.com",
            is_mentor=True,
        )
        app = _make_verified_mentor_app(email="onfile@example.com")
        response = self.client.get(
            reverse("apply_mentor_blocked", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already")

    # --- Mentor info ----------------------------------------------------

    def test_mentor_info_get_requires_verified_email(self):
        app = Application.objects.create(
            applicant_type=Application.Type.MENTOR,
            email="x@example.com",
        )
        response = self.client.get(
            reverse("apply_mentor_info", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("step3", response["Location"])

    def test_mentor_info_get_blocks_non_mentor_applicants(self):
        app = Application.objects.create(
            applicant_type=Application.Type.STUDENT,
            email="kid@example.com",
        )
        from django.utils import timezone

        app.email_verified_at = timezone.now()
        app.save()
        response = self.client.get(
            reverse("apply_mentor_info", kwargs={"app_id": app.application_id})
        )
        # Non-mentor should be redirected away (to their own flow).
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("/mentor/", response["Location"])

    def test_mentor_info_post_saves_and_advances(self):
        app = _make_verified_mentor_app()
        response = self.client.post(
            reverse("apply_mentor_info", kwargs={"app_id": app.application_id}),
            {
                "legal_first_name": "Alex",
                "first_name": "",
                "last_name": "Lee",
                "cell_phone": "555-444-1212",
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
        app = _make_verified_mentor_app()
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
        app = _make_verified_mentor_app()
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
        app = _make_verified_mentor_app()
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
        app = _make_verified_mentor_app()
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

    # --- Confirm + submit ----------------------------------------------

    def test_mentor_confirm_submission_sets_status_and_emails(self):
        app = _make_verified_mentor_app()
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
        # Lead mentor email exists in outbox.
        self.assertTrue(
            any("leads@girlsofsteelrobotics.org" in m.to for m in mail.outbox)
        )

    def test_mentor_submitted_page_renders(self):
        app = _make_verified_mentor_app()
        app.status = Application.Status.SUBMITTED
        app.save()
        response = self.client.get(
            reverse("apply_submitted", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, app.application_id)

    # --- Resume routing -------------------------------------------------

    def test_resume_link_routes_mentor_to_mentor_info_after_otp(self):
        app = _make_verified_mentor_app()
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
        app = _make_verified_mentor_app()
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
        app = _make_verified_mentor_app()
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
