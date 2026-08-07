"""Tests for parent-specific flows: handoff security, opt-in defaults, email prefill."""

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from applications.forms import ParentHandoffForm
from applications.models import Application
from programs.models import Program


class HandoffSecurityReproductionTests(TestCase):
    def test_student_cannot_bypass_handoff_by_resuming(self):
        app = Application.objects.create(
            applicant_type=Application.Type.STUDENT,
            email="student@example.com",
            email_verified_at=timezone.now(),
            current_step=6,
        )
        response = self.client.get(
            reverse("apply_step7", kwargs={"app_id": app.application_id})
        )
        self.assertContains(
            response, "Now an adult contact needs to finish the application"
        )
        response = self.client.post(
            reverse("apply_step7", kwargs={"app_id": app.application_id}),
            {"parent_email": "parent@example.com"},
        )
        app.refresh_from_db()
        self.assertEqual(app.status, Application.Status.AWAITING_PARENT)
        self.assertTrue("step7_handoff" in app.data)
        self.assertRedirects(response, reverse("apply_start"))
        response = self.client.post(
            reverse("apply_resume"), {"application_id": app.application_id}
        )
        self.assertRedirects(
            response, reverse("apply_step7", kwargs={"app_id": app.application_id})
        )
        response = self.client.get(response.url)
        self.assertContains(
            response, "This application has been handed off to an adult contact"
        )
        self.assertNotContains(response, "Please provide the primary adult contact")

    def test_parent_can_access_handoff_with_token(self):
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
        response = self.client.get(
            reverse(
                "apply_resume_link_with_token",
                kwargs={"app_id": app.application_id, "token": token},
            )
        )
        self.assertRedirects(
            response, reverse("apply_step7", kwargs={"app_id": app.application_id})
        )
        response = self.client.get(response.url)
        self.assertContains(response, "Please provide the primary adult contact")


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class ParentNotificationOptInReproductionTests(TestCase):
    def setUp(self):
        self.program = Program.objects.create(
            name="Spring 2030",
            start_date=timezone.localdate() + timezone.timedelta(days=60),
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
        self.assertContains(response, 'id="id_email_updates" checked', html=False)

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
        self.assertContains(response, 'id="id_email_updates"', html=False)
        self.assertNotContains(response, 'id="id_email_updates" checked', html=False)

    def test_both_parents_opted_out_blocked_at_step8(self):
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
                    "email_updates": False,
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
                "phone_number": "412-555-0100",
                "phone_type": "cell",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "At least one adult contact must opt in to receiving email updates",
        )
        app.refresh_from_db()
        self.assertEqual(app.current_step, 8)

    def test_step8_proceeds_when_primary_parent_opted_in(self):
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
                    "email_updates": True,
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
                "phone_number": "412-555-0100",
                "phone_type": "cell",
            },
        )
        self.assertRedirects(
            response,
            reverse("apply_step9", kwargs={"app_id": app.application_id}),
        )

    def test_cannot_submit_without_at_least_one_parent_opting_in(self):
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
                    "email_updates": False,
                },
                "step8-secondaryparent": {
                    "first_name": "Sam",
                    "last_name": "Spouse",
                    "relationship_to_student": "guardian",
                    "email_updates": False,
                },
            },
        )
        response = self.client.post(
            reverse("apply_step9", kwargs={"app_id": app.application_id}),
            {"confirm": "on"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "At least one parent or guardian must opt in to receiving email updates",
        )
        app.refresh_from_db()
        self.assertNotEqual(app.status, Application.Status.SUBMITTED)


class Step7PrefillEmailReproductionTests(TestCase):
    def test_parent_initiated_prefills_email(self):
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
        app = Application.objects.create(
            applicant_type=Application.Type.STUDENT,
            email="student@example.com",
            email_verified_at=timezone.now(),
            current_step=7,
            data={"step7_handoff": {"parent_email": "parent_handoff@example.com"}},
        )
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
