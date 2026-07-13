from __future__ import annotations

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from applications.models import Application


class DuplicateApplicationTests(TestCase):
    def test_new_behavior_detects_duplicate_and_allows_resume(self):
        # 1. Existing draft application for test@example.com
        old_app = Application.objects.create(
            email="test@example.com",
            email_verified_at=None,
            current_step=5,  # Further along
            status=Application.Status.DRAFT,
        )

        # 2. Start a new application
        response = self.client.post(reverse("apply_start"))
        new_app = Application.objects.exclude(pk=old_app.pk).get()

        # 3. Set email to the same as old_app
        self.client.post(
            reverse("apply_step2", kwargs={"app_id": new_app.application_id}),
            {"applicant_type": Application.Type.PARENT, "email": "test@example.com"},
        )
        new_app.refresh_from_db()

        # 4. Verify email (Step 3)
        code = new_app.issue_otp()
        response = self.client.post(
            reverse("apply_step3", kwargs={"app_id": new_app.application_id}),
            {"code": code},
        )

        # NEW BEHAVIOR: it should redirect to duplicate-found page
        self.assertRedirects(
            response,
            reverse("apply_duplicate_found", kwargs={"app_id": new_app.application_id}),
            fetch_redirect_response=False,
        )

        # 5. Check the duplicate found page
        response = self.client.get(
            reverse("apply_duplicate_found", kwargs={"app_id": new_app.application_id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, old_app.application_id)

        # 6. Choose "resume"
        response = self.client.post(
            reverse("apply_duplicate_found", kwargs={"app_id": new_app.application_id}),
            {"action": "resume"},
        )

        # Should redirect to the old app's current step (Step 5)
        self.assertRedirects(
            response,
            reverse("apply_step5", kwargs={"app_id": old_app.application_id}),
            fetch_redirect_response=False,
        )

        # New app should be deleted
        self.assertFalse(Application.objects.filter(pk=new_app.pk).exists())
        # Old app should still exist
        self.assertTrue(Application.objects.filter(pk=old_app.pk).exists())

    def test_new_behavior_allows_start_over(self):
        # 1. Existing draft application
        old_app = Application.objects.create(
            email="test2@example.com", status=Application.Status.DRAFT
        )

        # 2. New application
        app = Application.objects.create(email="test2@example.com")

        # 3. Choose "start over"
        response = self.client.post(
            reverse("apply_duplicate_found", kwargs={"app_id": app.application_id}),
            {"action": "start_over"},
        )

        # Should continue to Step 4 of the NEW application
        self.assertRedirects(
            response,
            reverse("apply_step4", kwargs={"app_id": app.application_id}),
            fetch_redirect_response=False,
        )

        # Old app should be deleted
        self.assertFalse(Application.objects.filter(pk=old_app.pk).exists())
        # New app should still exist
        self.assertTrue(Application.objects.filter(pk=app.pk).exists())

    def test_mentor_duplicate_resume_redirects_to_mentor_info(self):
        # 1. Existing draft MENTOR application
        old_app = Application.objects.create(
            email="mentor@example.com",
            applicant_type=Application.Type.MENTOR,
            status=Application.Status.DRAFT,
            email_verified_at=timezone.now(),
            current_step=4,
        )
        # Note: current_step=4 for mentors means they verified email already.
        # But here we are simulating that they started a NEW application and verified it.

        # 2. New application
        new_app = Application.objects.create(
            email="mentor@example.com", applicant_type=Application.Type.MENTOR
        )

        # 3. Resume
        response = self.client.post(
            reverse("apply_duplicate_found", kwargs={"app_id": new_app.application_id}),
            {"action": "resume"},
        )

        # Should redirect to mentor_info of old_app
        self.assertRedirects(
            response,
            reverse("apply_mentor_info", kwargs={"app_id": old_app.application_id}),
            fetch_redirect_response=False,
        )

    def test_mentor_start_over_redirects_to_mentor_info(self):
        # 1. New mentor application
        app = Application.objects.create(
            email="mentor2@example.com", applicant_type=Application.Type.MENTOR
        )
        # Existing one
        Application.objects.create(
            email="mentor2@example.com", status=Application.Status.DRAFT
        )

        # 2. Start over
        response = self.client.post(
            reverse("apply_duplicate_found", kwargs={"app_id": app.application_id}),
            {"action": "start_over"},
        )

        # Should continue to mentor_info step (not step 4)
        self.assertRedirects(
            response,
            reverse("apply_mentor_info", kwargs={"app_id": app.application_id}),
            fetch_redirect_response=False,
        )
