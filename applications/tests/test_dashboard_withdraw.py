"""Tests for the dashboard "Pending Applications" box and the applicant
withdraw flow (withdraw = permanently delete their own application).

An application is considered "tied" to a logged-in user when:

- Student: ``Application.email`` matches the student's personal or andrew email.
- Parent / adult: ``Application.email`` matches the adult's email, OR the
  adult's email is listed as the primary / secondary parent on the
  application, OR it is the handoff recipient of an AWAITING_PARENT app.

Only non-terminal statuses (draft, email_verified, awaiting_parent,
submitted, approved, approved_signed) are shown as "pending".
"""

from django.contrib.auth.models import AnonymousUser, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from applications.models import Application
from applications.services import PENDING_STATUSES, applications_for_user
from programs.models import Adult, Student


def _student_user(username="student_user"):
    return User.objects.create_user(
        username=username, password="password123"  # nosec B106
    )


def _parent_user(username="parent_user"):
    return User.objects.create_user(
        username=username, password="password123"  # nosec B106
    )


class ApplicationsForUserTests(TestCase):
    def setUp(self):
        self.student_user = _student_user()
        self.student = Student.objects.create(
            user=self.student_user,
            first_name="Student",
            last_name="One",
            personal_email="student@example.com",
        )
        self.parent_user = _parent_user()
        self.parent = Adult.objects.create(
            user=self.parent_user,
            first_name="Parent",
            last_name="One",
            personal_email="parent@example.com",
            is_parent=True,
        )
        self.other_user = User.objects.create_user(
            username="other_user", password="password123"  # nosec B106
        )

    def _app(self, **kwargs):
        defaults = {
            "applicant_type": Application.Type.STUDENT,
            "email": "student@example.com",
            "status": Application.Status.DRAFT,
        }
        defaults.update(kwargs)
        return Application.objects.create(**defaults)

    def test_student_sees_application_started_with_their_email(self):
        app = self._app()
        self.assertIn(app, applications_for_user(self.student_user))

    def test_student_does_not_see_unrelated_applications(self):
        self._app(email="someone-else@example.com")
        self.assertEqual(applications_for_user(self.student_user), [])

    def test_parent_sees_application_they_started(self):
        app = Application.objects.create(
            applicant_type=Application.Type.PARENT,
            email="parent@example.com",
            status=Application.Status.SUBMITTED,
        )
        self.assertIn(app, applications_for_user(self.parent_user))

    def test_parent_sees_application_awaiting_their_handoff(self):
        app = self._app(
            status=Application.Status.AWAITING_PARENT,
            data={"step7_handoff": {"parent_email": "PARENT@example.com"}},
        )
        self.assertIn(app, applications_for_user(self.parent_user))

    def test_parent_sees_application_listing_them_as_primary_or_secondary(self):
        primary = self._app(
            status=Application.Status.EMAIL_VERIFIED,
            data={"step7-primaryparent": {"email": "parent@example.com"}},
        )
        secondary = self._app(
            status=Application.Status.EMAIL_VERIFIED,
            data={"step8-secondaryparent": {"email": "parent@example.com"}},
        )
        result = applications_for_user(self.parent_user)
        self.assertIn(primary, result)
        self.assertIn(secondary, result)

    def test_terminated_applications_are_excluded(self):
        converted = self._app(status=Application.Status.CONVERTED)
        declined = self._app(status=Application.Status.DECLINED)
        result = applications_for_user(self.student_user)
        self.assertNotIn(converted, result)
        self.assertNotIn(declined, result)

    def test_pending_statuses_include_in_progress_states(self):
        expected = {
            Application.Status.DRAFT,
            Application.Status.EMAIL_VERIFIED,
            Application.Status.AWAITING_PARENT,
            Application.Status.SUBMITTED,
            Application.Status.APPROVED,
            Application.Status.APPROVED_SIGNED,
        }
        self.assertEqual(set(PENDING_STATUSES), expected)

    def test_unauthenticated_user_gets_empty_list(self):
        self.assertEqual(applications_for_user(AnonymousUser()), [])


class DashboardPendingApplicationsTests(TestCase):
    def setUp(self):
        self.student_user = _student_user()
        self.student = Student.objects.create(
            user=self.student_user,
            first_name="Student",
            last_name="One",
            personal_email="student@example.com",
        )

    def test_dashboard_renders_pending_applications_box(self):
        app = Application.objects.create(
            applicant_type=Application.Type.STUDENT,
            email="student@example.com",
            status=Application.Status.DRAFT,
            current_step=5,
        )
        self.client.force_login(self.student_user)
        response = self.client.get(reverse("profile_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pending Applications")
        self.assertContains(response, app.application_id)
        self.assertContains(
            response,
            reverse("apply_continue", kwargs={"app_id": app.application_id}),
        )
        self.assertContains(
            response,
            reverse("apply_withdraw", kwargs={"app_id": app.application_id}),
        )

    def test_dashboard_hides_box_when_no_pending_applications(self):
        self.client.force_login(self.student_user)
        response = self.client.get(reverse("profile_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Pending Applications")


class ApplicationWithdrawViewTests(TestCase):
    def setUp(self):
        self.student_user = _student_user()
        self.student = Student.objects.create(
            user=self.student_user,
            first_name="Student",
            last_name="One",
            personal_email="student@example.com",
        )
        self.other_user = _student_user(username="other_user")
        self.app = Application.objects.create(
            applicant_type=Application.Type.STUDENT,
            email="student@example.com",
            status=Application.Status.DRAFT,
            current_step=5,
        )

    def test_get_shows_confirmation_page(self):
        self.client.force_login(self.student_user)
        response = self.client.get(
            reverse("apply_withdraw", kwargs={"app_id": self.app.application_id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Withdraw")
        self.assertContains(response, self.app.application_id)

    def test_post_deletes_application_and_redirects_to_dashboard(self):
        self.client.force_login(self.student_user)
        response = self.client.post(
            reverse("apply_withdraw", kwargs={"app_id": self.app.application_id})
        )
        self.assertRedirects(response, reverse("profile_dashboard"))
        self.assertFalse(Application.objects.filter(pk=self.app.pk).exists())

    def test_unrelated_user_cannot_withdraw(self):
        self.client.force_login(self.other_user)
        response = self.client.post(
            reverse("apply_withdraw", kwargs={"app_id": self.app.application_id})
        )
        self.assertTrue(Application.objects.filter(pk=self.app.pk).exists())
        self.assertRedirects(response, reverse("profile_dashboard"))

    def test_terminated_application_cannot_be_withdrawn(self):
        self.app.status = Application.Status.CONVERTED
        self.app.save(update_fields=["status", "updated_at"])
        self.client.force_login(self.student_user)
        self.client.post(
            reverse("apply_withdraw", kwargs={"app_id": self.app.application_id})
        )
        self.assertTrue(Application.objects.filter(pk=self.app.pk).exists())

    def test_withdraw_requires_login(self):
        response = self.client.get(
            reverse("apply_withdraw", kwargs={"app_id": self.app.application_id})
        )
        self.assertEqual(response.status_code, 302)


class HandoffResumeFromDashboardTests(TestCase):
    """A parent whose email matches the handoff recipient should be able to
    resume an AWAITING_PARENT application straight from the dashboard link."""

    def setUp(self):
        self.parent_user = _parent_user()
        self.parent = Adult.objects.create(
            user=self.parent_user,
            first_name="Parent",
            last_name="One",
            personal_email="parent@example.com",
            is_parent=True,
        )
        self.app = Application.objects.create(
            applicant_type=Application.Type.STUDENT,
            email="student@example.com",
            email_verified_at=timezone.now(),
            current_step=7,
            status=Application.Status.AWAITING_PARENT,
            data={"step7_handoff": {"parent_email": "parent@example.com"}},
        )
        self.app.issue_handoff_token()

    def test_parent_resume_grants_handoff_access(self):
        self.client.force_login(self.parent_user)
        response = self.client.get(
            reverse("apply_continue", kwargs={"app_id": self.app.application_id})
        )
        self.assertRedirects(
            response, reverse("apply_step7", kwargs={"app_id": self.app.application_id})
        )
        response = self.client.get(response.url)
        self.assertContains(response, "Please provide the primary adult contact")

    def test_unrelated_parent_cannot_resume_handoff_app(self):
        other = User.objects.create_user(
            username="other_parent", password="password123"  # nosec B106
        )
        Adult.objects.create(
            user=other,
            first_name="Other",
            last_name="Parent",
            personal_email="other@example.com",
            is_parent=True,
        )
        self.client.force_login(other)
        response = self.client.get(
            reverse("apply_continue", kwargs={"app_id": self.app.application_id})
        )
        self.assertRedirects(
            response, reverse("apply_step7", kwargs={"app_id": self.app.application_id})
        )
        response = self.client.get(response.url)
        self.assertContains(
            response, "This application has been handed off to an adult contact"
        )
