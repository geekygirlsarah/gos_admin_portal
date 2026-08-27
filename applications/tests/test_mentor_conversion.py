"""Tests for converting an approved mentor application into an Adult record."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from applications.models import Application
from applications.services import (
    ApplicationConversionError,
    convert_application_to_student,
)
from programs.models import Adult


def _approved_mentor_app(**overrides) -> Application:
    """An APPROVED mentor application with full mentor_info data."""
    data = {
        "mentor_info": {
            "legal_first_name": "Alex",
            "preferred_first_name": "Alex",
            "last_name": "Lee",
            "pronouns": "they/them",
            "phone_number": "555-444-1212",
            "phone_type": "cell",
            "can_receive_texts": True,
            "discord_username": "alexlee",
            "andrew_id": "alexlee",
            "employer": "Acme Robotics",
            "notes": "Excited to help.",
        },
        "mentor_clearance_interest": {"interested": "yes"},
        "mentor_clearance_detail": {"paca": "have", "patch": "need", "fbi": "need"},
    }
    defaults = {
        "applicant_type": Application.Type.MENTOR,
        "email": "mentor@example.com",
        "status": Application.Status.APPROVED,
        "data": data,
    }
    defaults.update(overrides)
    return Application.objects.create(**defaults)


class MentorConversionTests(TestCase):
    def test_conversion_creates_adult_without_program(self):
        """Mentors don't pick a program, so conversion must not require one."""
        app = _approved_mentor_app()

        result = convert_application_to_student(app)

        self.assertIsInstance(result, Adult)
        self.assertTrue(result.is_mentor)
        app.refresh_from_db()
        self.assertEqual(app.status, Application.Status.CONVERTED)
        self.assertIsNotNone(app.converted_at)

    def test_conversion_requires_approved_status(self):
        app = _approved_mentor_app(status=Application.Status.SUBMITTED)
        with self.assertRaises(ApplicationConversionError):
            convert_application_to_student(app)

    def test_conversion_maps_mentor_fields_to_adult(self):
        app = _approved_mentor_app()

        adult = convert_application_to_student(app)

        self.assertEqual(adult.legal_first_name, "Alex")
        self.assertEqual(adult.preferred_first_name, "Alex")
        self.assertEqual(adult.last_name, "Lee")
        self.assertEqual(adult.personal_email, "mentor@example.com")
        self.assertEqual(adult.pronouns, "they/them")
        self.assertEqual(adult.phone_number, "555-444-1212")
        self.assertEqual(adult.phone_type, "cell")
        self.assertTrue(adult.can_receive_texts)
        self.assertEqual(adult.discord_username, "alexlee")
        self.assertEqual(adult.andrew_id, "alexlee")
        self.assertEqual(adult.andrew_email, "alexlee@andrew.cmu.edu")
        self.assertEqual(adult.employer, "Acme Robotics")
        self.assertEqual(adult.notes, "Excited to help.")

    def test_conversion_does_not_flag_mentor_as_parent(self):
        app = _approved_mentor_app()
        adult = convert_application_to_student(app)
        self.assertFalse(adult.is_parent)

    def test_conversion_uses_verified_email_when_no_form_email(self):
        app = _approved_mentor_app(
            email="verified@example.com",
            data={
                "mentor_info": {
                    "legal_first_name": "Alex",
                    "last_name": "Lee",
                },
            },
        )
        adult = convert_application_to_student(app)
        self.assertEqual(adult.personal_email, "verified@example.com")

    def test_conversion_is_idempotent(self):
        app = _approved_mentor_app(status=Application.Status.APPROVED_SIGNED)

        first = convert_application_to_student(app)
        second = convert_application_to_student(app)

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Adult.objects.filter(is_mentor=True).count(), 1)


class MentorApprovedEmailTests(TestCase):
    """The verified-email -> Andrew-email fallback only applies to Andrew emails."""

    def test_andrew_email_fallback_from_verified_email(self):
        app = _approved_mentor_app(
            email="alexlee@andrew.cmu.edu",
            data={
                "mentor_info": {
                    "legal_first_name": "Alex",
                    "last_name": "Lee",
                },
            },
        )
        adult = convert_application_to_student(app)
        self.assertEqual(adult.personal_email, "alexlee@andrew.cmu.edu")
        self.assertEqual(adult.andrew_email, "alexlee@andrew.cmu.edu")


User = get_user_model()


def _ensure_review_permission():
    from django.contrib.contenttypes.models import ContentType

    ct, _ = ContentType.objects.get_or_create(
        app_label="applications", model="application"
    )
    perm, _ = Permission.objects.get_or_create(
        content_type=ct,
        codename="review_application",
        defaults={"name": "Can review applications"},
    )
    return perm


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class MentorReviewToConversionStoryTest(TestCase):
    """End-to-end: submitted mentor application -> approve -> convert."""

    def _reviewer(self):
        user = User.objects.create_user(username="lead", email="lead@x.test")
        group, _ = Group.objects.get_or_create(name="LeadMentor")
        group.permissions.add(_ensure_review_permission())
        user.groups.add(group)
        return user

    def _submitted_mentor_app(self):
        return Application.objects.create(
            applicant_type=Application.Type.MENTOR,
            email="newmentor@example.com",
            email_verified_at=timezone.now(),
            status=Application.Status.SUBMITTED,
            submitted_at=timezone.now(),
            current_step=9,
            data={
                "mentor_info": {
                    "legal_first_name": "Alex",
                    "preferred_first_name": "Alex",
                    "last_name": "Lee",
                    "discord_username": "alexlee",
                    "andrew_id": "alexlee",
                    "employer": "Acme Robotics",
                    "notes": "Excited to help.",
                },
                "mentor_clearance_interest": {"interested": "no"},
            },
        )

    def test_approve_then_convert_creates_mentor_adult(self):
        mail.outbox = []
        app = self._submitted_mentor_app()
        self.client.force_login(self._reviewer())

        approve = self.client.post(
            reverse("application_review_approve", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(approve.status_code, 302)
        app.refresh_from_db()
        self.assertEqual(app.status, Application.Status.APPROVED)

        convert = self.client.post(
            reverse("application_review_convert", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(convert.status_code, 302)
        app.refresh_from_db()
        self.assertEqual(app.status, Application.Status.CONVERTED)

        adult = Adult.objects.get(is_mentor=True)
        self.assertEqual(adult.legal_first_name, "Alex")
        self.assertEqual(adult.last_name, "Lee")
        self.assertEqual(adult.personal_email, "newmentor@example.com")
        self.assertFalse(adult.is_parent)
        self.assertEqual(adult.discord_username, "alexlee")
        self.assertEqual(adult.employer, "Acme Robotics")

    def test_convert_button_not_available_until_approved(self):
        app = self._submitted_mentor_app()
        self.client.force_login(self._reviewer())
        response = self.client.get(
            reverse("application_review_detail", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Convert to Mentor")
        self.assertContains(response, "disabled")
