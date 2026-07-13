"""Tests for resending emails from the application review detail page."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from applications.models import Application

User = get_user_model()

LEAD_MENTORS_GROUP = "Lead Mentors"
REVIEW_PERM_CODENAME = "review_application"


def _make_application(**overrides):
    defaults = dict(
        applicant_type=Application.Type.PARENT,
        email="parent@example.com",
        current_step=3,
        status=Application.Status.DRAFT,
        data={
            "step5-student": {
                "legal_first_name": "Ada",
                "last_name": "Lovelace",
                "email": "ada@example.com",
            },
            "step7-primaryparent": {
                "first_name": "Pat",
                "last_name": "Parent",
                "email": "parent@example.com",
            },
        },
    )
    defaults.update(overrides)
    return Application.objects.create(**defaults)


def _ensure_review_perm():
    from django.contrib.contenttypes.models import ContentType

    ct, _ = ContentType.objects.get_or_create(
        app_label="applications", model="application"
    )
    perm, _ = Permission.objects.get_or_create(
        content_type=ct,
        codename=REVIEW_PERM_CODENAME,
        defaults={"name": "Can review applications"},
    )
    return perm


def _ensure_lead_mentors_group():
    group, _ = Group.objects.get_or_create(name=LEAD_MENTORS_GROUP)
    group.permissions.add(_ensure_review_perm())
    return group


def _reviewer_user(username="lead"):
    user = User.objects.create_user(username=username, email=f"{username}@x.test")
    user.groups.add(_ensure_lead_mentors_group())
    return user


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class ResendEmailTests(TestCase):
    def setUp(self):
        self.app = _make_application()
        self.user = _reviewer_user()
        self.client.force_login(self.user)
        mail.outbox = []

    def test_resend_otp_email(self):
        url = reverse(
            "application_review_resend_email",
            kwargs={"app_id": self.app.application_id},
        )
        response = self.client.post(url, {"type": "otp"})
        self.assertEqual(response.status_code, 302)
        # Should have sent 1 email
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("verification code", mail.outbox[0].subject.lower())
        self.assertIn("parent@example.com", mail.outbox[0].to)

    def test_resend_handoff_email(self):
        # Handoff email is usually sent when student fills it out and gives parent email.
        self.app.status = Application.Status.AWAITING_PARENT
        self.app.save()

        url = reverse(
            "application_review_resend_email",
            kwargs={"app_id": self.app.application_id},
        )
        response = self.client.post(url, {"type": "handoff"})
        self.assertEqual(response.status_code, 302)
        # Should have sent 1 email
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("application is waiting for you", mail.outbox[0].subject.lower())
        self.assertIn("parent@example.com", mail.outbox[0].to)

    def test_resend_submission_confirmation(self):
        self.app.status = Application.Status.SUBMITTED
        self.app.submitted_at = timezone.now()
        self.app.save()

        url = reverse(
            "application_review_resend_email",
            kwargs={"app_id": self.app.application_id},
        )
        response = self.client.post(url, {"type": "submitted"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("application has been submitted", mail.outbox[0].subject.lower())

    def test_invalid_type_error(self):
        url = reverse(
            "application_review_resend_email",
            kwargs={"app_id": self.app.application_id},
        )
        response = self.client.post(url, {"type": "invalid"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 0)
