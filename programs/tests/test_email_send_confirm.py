"""Confirm-before-send modal on the bulk email pages.

Each "Email Program"-style page (program messaging, applicant messaging,
and balance-sheet email) must render a confirmation modal that summarizes
the audience, the outgoing sender address, and the subject, previews the
message, and requires an explicit "written on behalf of the team"
acknowledgment before the send can be confirmed.
"""

from django.contrib.auth.models import ContentType, Group, Permission, User
from django.test import TestCase
from django.urls import reverse

from programs.models import Program

MODAL_MARKERS = [
    # The modal itself
    'id="emailSendConfirmModal"',
    # Audience summary row
    'id="email-confirm-audience-label"',
    "data-email-confirm-audience",
    # Sender address row
    'id="email-confirm-sender-label"',
    "data-email-confirm-sender",
    # Subject row
    'id="email-confirm-subject-label"',
    "data-email-confirm-subject",
    # Message preview + on-behalf acknowledgment
    "data-email-confirm-preview",
    'id="email-confirm-on-behalf"',
    # Confirm/cancel buttons
    'id="email-confirm-send"',
]


class EmailSendConfirmModalTests(TestCase):
    def _assert_modal_present(self, response):
        self.assertEqual(response.status_code, 200)
        for marker in MODAL_MARKERS:
            with self.subTest(marker=marker):
                self.assertContains(response, marker)

    def _login_reviewer(self):
        """Create a user with the application review permission and log in."""
        ct, _ = ContentType.objects.get_or_create(
            app_label="applications", model="application"
        )
        perm, _ = Permission.objects.get_or_create(
            content_type=ct,
            codename="review_application",
            defaults={"name": "Can review applications"},
        )
        User.objects.create_user(
            username="reviewer", password="password123"  # nosec B106
        ).user_permissions.add(perm)
        self.client.login(username="reviewer", password="password123")  # nosec B106

    def test_program_email_page_shows_confirm_modal(self):
        program = Program.objects.create(name="Test Program", active=True)
        mentor = User.objects.create_user(
            username="mentor_user", password="password123"  # nosec B106
        )
        group, _ = Group.objects.get_or_create(name="Mentor")
        mentor.groups.add(group)
        self.client.login(username="mentor_user", password="password123")  # nosec B106
        response = self.client.get(reverse("program_email", args=[program.pk]))
        self._assert_modal_present(response)

    def test_program_messaging_page_shows_confirm_modal(self):
        User.objects.create_superuser(
            username="lead_mentor", password="password123"  # nosec B106
        )
        self.client.login(username="lead_mentor", password="password123")  # nosec B106
        response = self.client.get(reverse("program_messaging"))
        self._assert_modal_present(response)

    def test_program_email_balances_page_shows_confirm_modal(self):
        program = Program.objects.create(name="Test Program", active=True)
        User.objects.create_superuser(
            username="lead_mentor", password="password123"  # nosec B106
        )
        self.client.login(username="lead_mentor", password="password123")  # nosec B106
        response = self.client.get(reverse("program_dues_email", args=[program.pk]))
        self._assert_modal_present(response)

    def test_application_review_messaging_shows_confirm_modal(self):
        self._login_reviewer()
        response = self.client.get(reverse("application_review_messaging"))
        self._assert_modal_present(response)
