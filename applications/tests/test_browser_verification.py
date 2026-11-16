import time
from unittest.mock import patch

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from applications.models import Application


class BrowserVerificationTests(TestCase):
    def setUp(self):
        self.application = Application.objects.create(
            applicant_type=Application.Type.PARENT,
            email="parent@example.test",
            email_verified_at=timezone.now(),
            current_step=5,
            status=Application.Status.EMAIL_VERIFIED,
            data={"step5-student": {"legal_first_name": "Private"}},
        )
        self.args = {"app_id": self.application.application_id}
        self.access = reverse("apply_browser_verification", kwargs=self.args)

    def verify(self, client, code="123456"):
        with patch("applications.views.browser.generate_otp_code", return_value=code):
            with patch("applications.views.browser.send_otp_email") as send:
                client.post(self.access, {"action": "send"})
        response = client.post(self.access, {"code": code})
        return response, send

    def test_existing_application_requires_email_in_each_browser(self):
        for name in ("apply_step2", "apply_step4", "apply_step5", "apply_submitted"):
            response = self.client.get(reverse(name, kwargs=self.args))
            self.assertRedirects(response, self.access, fetch_redirect_response=False)
        response = self.client.post(
            reverse("apply_step2", kwargs=self.args),
            {"applicant_type": "mentor", "email": "attacker@example.test"},
        )
        self.assertRedirects(response, self.access, fetch_redirect_response=False)
        self.application.refresh_from_db()
        self.assertEqual(self.application.email, "parent@example.test")
        self.assertEqual(self.application.applicant_type, Application.Type.PARENT)

        response, send = self.verify(self.client)
        self.assertEqual(send.call_args.kwargs["recipient"], "parent@example.test")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            self.client.get(reverse("apply_step5", kwargs=self.args)).status_code, 200
        )
        other = Client()
        self.assertRedirects(
            other.get(reverse("apply_step5", kwargs=self.args)),
            self.access,
            fetch_redirect_response=False,
        )

    def test_handoff_verifies_parent_not_student(self):
        self.application.applicant_type = Application.Type.STUDENT
        self.application.email = "student@example.test"
        verified_at = self.application.email_verified_at
        self.application.status = Application.Status.AWAITING_PARENT
        self.application.current_step = 7
        self.application.data = {
            "step7_handoff": {"parent_email": "parent@example.test"}
        }
        self.application.issue_handoff_token()
        self.application.save()
        response, send = self.verify(self.client)
        self.assertEqual(send.call_args.kwargs["recipient"], "parent@example.test")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            self.client.get(reverse("apply_step7", kwargs=self.args)).status_code, 200
        )
        self.application.refresh_from_db()
        self.assertEqual(self.application.email_verified_at, verified_at)

    def test_wrong_code_and_changed_recipient_do_not_grant_access(self):
        with patch(
            "applications.views.browser.generate_otp_code", return_value="123456"
        ):
            with patch("applications.views.browser.send_otp_email"):
                self.client.post(self.access, {"action": "send"})
        self.client.post(self.access, {"code": "654321"})
        self.assertRedirects(
            self.client.get(reverse("apply_step5", kwargs=self.args)),
            self.access,
            fetch_redirect_response=False,
        )
        self.application.email = "changed@example.test"
        self.application.save()
        self.client.post(self.access, {"code": "123456"})
        self.assertRedirects(
            self.client.get(reverse("apply_step5", kwargs=self.args)),
            self.access,
            fetch_redirect_response=False,
        )

    def test_expired_code_and_replay_are_rejected(self):
        with patch(
            "applications.views.browser.generate_otp_code", return_value="123456"
        ):
            with patch("applications.views.browser.send_otp_email"):
                self.client.post(self.access, {"action": "send"})
        session = self.client.session
        key = (
            f"application_browser_challenge:{self.application.organization_id}:"
            f"{self.application.application_id}"
        )
        challenge = session[key]
        challenge["expires_at"] = int(time.time()) - 1
        session[key] = challenge
        session.save()
        self.client.post(self.access, {"code": "123456"})
        self.assertRedirects(
            self.client.get(reverse("apply_step5", kwargs=self.args)),
            self.access,
            fetch_redirect_response=False,
        )
        self.verify(self.client)
        session = self.client.session
        session.pop(
            f"application_browser_access:{self.application.organization_id}:"
            f"{self.application.application_id}"
        )
        session.save()
        self.client.post(self.access, {"code": "123456"})
        self.assertRedirects(
            self.client.get(reverse("apply_step5", kwargs=self.args)),
            self.access,
            fetch_redirect_response=False,
        )

    def test_missing_handoff_email_requires_assistance(self):
        self.application.applicant_type = Application.Type.STUDENT
        self.application.status = Application.Status.AWAITING_PARENT
        self.application.data = {}
        self.application.save()
        response = self.client.get(self.access)
        self.assertContains(response, "contact your program", status_code=400)
        with patch("applications.views.browser.send_otp_email") as send:
            response = self.client.post(self.access, {"action": "send"})
        self.assertEqual(response.status_code, 400)
        send.assert_not_called()
