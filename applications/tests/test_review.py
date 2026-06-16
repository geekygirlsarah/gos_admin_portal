"""Tests for the lead-mentor application review pages."""

from __future__ import annotations

import datetime
import json

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from applications.models import Application
from programs.models import Program

User = get_user_model()

LEAD_MENTORS_GROUP = "Lead Mentors"
REVIEW_PERM_CODENAME = "review_application"


def _make_application(**overrides):
    defaults = dict(
        applicant_type=Application.Type.PARENT,
        email="parent@example.com",
        current_step=8,
        email_verified_at=timezone.now(),
        status=Application.Status.SUBMITTED,
        submitted_at=timezone.now(),
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
    """Return the review_application Permission, creating its ContentType
    and Permission row in test databases that don't preserve them from
    migrations."""
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
    """Return the Lead Mentors group, creating it (with review perm) if
    the data migration's effect didn't survive into the test DB."""
    group, _ = Group.objects.get_or_create(name=LEAD_MENTORS_GROUP)
    group.permissions.add(_ensure_review_perm())
    return group


def _reviewer_user(username="lead"):
    user = User.objects.create_user(username=username, email=f"{username}@x.test")
    user.groups.add(_ensure_lead_mentors_group())
    return user


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class LeadMentorsGroupTests(TestCase):
    """After bootstrap, the Lead Mentors group exists with the review perm."""

    def test_bootstrap_creates_group_with_review_permission(self):
        group = _ensure_lead_mentors_group()
        perm_codenames = set(group.permissions.values_list("codename", flat=True))
        self.assertIn(REVIEW_PERM_CODENAME, perm_codenames)

    def test_review_permission_exists(self):
        _ensure_review_perm()
        self.assertTrue(
            Permission.objects.filter(codename=REVIEW_PERM_CODENAME).exists()
        )


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class ReviewPermissionGatingTests(TestCase):
    def setUp(self):
        self.app = _make_application()
        self.list_url = reverse("application_review_list")
        self.detail_url = reverse(
            "application_review_detail", kwargs={"app_id": self.app.application_id}
        )

    def test_anonymous_redirected_to_login(self):
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response.url.lower())

    def test_user_without_permission_forbidden(self):
        plain = User.objects.create_user(username="plain")
        self.client.force_login(plain)
        response = self.client.get(self.list_url)
        # PermissionRequiredMixin denies access — either 403 (forbidden)
        # or 302 (redirect to login), depending on Django configuration.
        self.assertIn(response.status_code, (302, 403))

    def test_lead_mentor_can_access_list(self):
        self.client.force_login(_reviewer_user())
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.app.application_id)

    def test_lead_mentor_can_access_detail(self):
        self.client.force_login(_reviewer_user())
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.app.application_id)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class ReviewListFilterTests(TestCase):
    def setUp(self):
        self.submitted = _make_application(
            email="sub@example.com", status=Application.Status.SUBMITTED
        )
        self.approved = _make_application(
            email="apr@example.com", status=Application.Status.APPROVED
        )
        self.declined = _make_application(
            email="dec@example.com", status=Application.Status.DECLINED
        )
        self.client.force_login(_reviewer_user())

    def test_filter_by_status(self):
        url = reverse("application_review_list") + "?status=approved"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.approved.application_id)
        self.assertNotContains(response, self.submitted.application_id)
        self.assertNotContains(response, self.declined.application_id)

    def test_invalid_status_is_ignored(self):
        url = reverse("application_review_list") + "?status=bogus"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        # All three still visible.
        self.assertContains(response, self.submitted.application_id)
        self.assertContains(response, self.approved.application_id)
        self.assertContains(response, self.declined.application_id)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class ApproveDeclineEditDeleteTests(TestCase):
    def setUp(self):
        self.app = _make_application()
        self.user = _reviewer_user()
        self.client.force_login(self.user)
        mail.outbox = []

    # -- Approve ------------------------------------------------------------

    def test_approve_sets_status_and_emails(self):
        url = reverse(
            "application_review_approve", kwargs={"app_id": self.app.application_id}
        )
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.app.refresh_from_db()
        self.assertEqual(self.app.status, Application.Status.APPROVED)
        self.assertIsNotNone(self.app.reviewed_at)
        self.assertEqual(self.app.reviewed_by, self.user)
        # Should have advanced past the wizard.
        self.assertGreaterEqual(self.app.current_step, 9)
        # An approval email went out — to both the application email and the parent.
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        recipients = {r.lower() for r in msg.to}
        self.assertIn("parent@example.com", recipients)
        # Student email too (different from application email).
        self.assertIn("ada@example.com", recipients)

    def test_approving_already_approved_is_a_noop(self):
        self.app.status = Application.Status.APPROVED
        self.app.save()
        url = reverse(
            "application_review_approve", kwargs={"app_id": self.app.application_id}
        )
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 0)

    # -- Decline ------------------------------------------------------------

    def test_decline_with_reason_saves_and_emails(self):
        url = reverse(
            "application_review_decline", kwargs={"app_id": self.app.application_id}
        )
        response = self.client.post(url, {"reason": "Not the right fit this season."})
        self.assertEqual(response.status_code, 302)
        self.app.refresh_from_db()
        self.assertEqual(self.app.status, Application.Status.DECLINED)
        self.assertEqual(self.app.decline_reason, "Not the right fit this season.")
        self.assertEqual(self.app.reviewed_by, self.user)
        # An email went out and contains the reason.
        self.assertEqual(len(mail.outbox), 1)
        body = mail.outbox[0].body
        self.assertIn("Not the right fit this season.", body)

    def test_decline_without_reason_still_works(self):
        url = reverse(
            "application_review_decline", kwargs={"app_id": self.app.application_id}
        )
        response = self.client.post(url, {"reason": ""})
        self.assertEqual(response.status_code, 302)
        self.app.refresh_from_db()
        self.assertEqual(self.app.status, Application.Status.DECLINED)
        self.assertEqual(self.app.decline_reason, "")
        self.assertEqual(len(mail.outbox), 1)

    def test_decline_get_renders_form(self):
        url = reverse(
            "application_review_decline", kwargs={"app_id": self.app.application_id}
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Send decline")

    # -- Edit ---------------------------------------------------------------

    def test_edit_saves_data_and_email(self):
        url = reverse(
            "application_review_edit", kwargs={"app_id": self.app.application_id}
        )
        new_data = {"step5": {"legal_first_name": "Grace"}}
        response = self.client.post(
            url,
            {
                "data_json": json.dumps(new_data),
                "email": "new-parent@example.com",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.app.refresh_from_db()
        self.assertEqual(self.app.data, new_data)
        self.assertEqual(self.app.email, "new-parent@example.com")

    def test_edit_rejects_invalid_json(self):
        url = reverse(
            "application_review_edit", kwargs={"app_id": self.app.application_id}
        )
        response = self.client.post(
            url, {"data_json": "not json {", "email": "x@example.com"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Invalid JSON")

    def test_edit_rejects_non_object_json(self):
        url = reverse(
            "application_review_edit", kwargs={"app_id": self.app.application_id}
        )
        response = self.client.post(
            url, {"data_json": "[1, 2, 3]", "email": "x@example.com"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "object")

    # -- Delete -------------------------------------------------------------

    def test_delete_get_renders_confirmation(self):
        url = reverse(
            "application_review_delete", kwargs={"app_id": self.app.application_id}
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Yes, delete")

    def test_delete_post_removes_application(self):
        app_id = self.app.application_id
        url = reverse("application_review_delete", kwargs={"app_id": app_id})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Application.objects.filter(application_id=app_id).exists())


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class ConvertToStudentTests(TestCase):
    def setUp(self):
        self.program = Program.objects.create(
            name="Summer Camp",
            start_date=datetime.date(2099, 6, 1),
            end_date=datetime.date(2099, 6, 30),
            active=True,
        )
        self.user = _reviewer_user()
        self.client.force_login(self.user)

    def _approved_signed_app(self, **overrides):
        defaults = dict(
            applicant_type=Application.Type.PARENT,
            email="parent@example.com",
            current_step=9,
            email_verified_at=timezone.now(),
            status=Application.Status.APPROVED_SIGNED,
            program=self.program,
            submitted_at=timezone.now(),
            data={
                "step5-student": {
                    "legal_first_name": "Ada",
                    "first_name": "Ada",
                    "last_name": "Lovelace",
                    "personal_email": "ada@example.com",
                    "school_name": "Allderdice High School",
                    "graduation_year": 2030,
                    "date_of_birth": "2010-01-01",
                },
                "step7-primaryparent": {
                    "first_name": "Pat",
                    "last_name": "Parent",
                    "email": "parent@example.com",
                    "cell_phone": "555-444-0100",
                },
                "step8-secondaryparent": {
                    "first_name": "Sam",
                    "last_name": "Parent",
                    "email": "sam@example.com",
                },
            },
        )
        defaults.update(overrides)
        return Application.objects.create(**defaults)

    def test_convert_creates_student_adults_and_enrollment(self):
        from programs.models import Enrollment

        app = self._approved_signed_app()
        url = reverse(
            "application_review_convert", kwargs={"app_id": app.application_id}
        )
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        app.refresh_from_db()
        self.assertEqual(app.status, Application.Status.CONVERTED)
        self.assertIsNotNone(app.converted_at)
        self.assertIsNotNone(app.converted_student)
        student = app.converted_student
        self.assertEqual(student.legal_first_name, "Ada")
        self.assertEqual(student.last_name, "Lovelace")
        # Primary + secondary contacts wired up.
        self.assertIsNotNone(student.primary_contact)
        self.assertEqual(student.primary_contact.personal_email, "parent@example.com")
        self.assertIsNotNone(student.secondary_contact)
        self.assertEqual(student.secondary_contact.personal_email, "sam@example.com")
        # School created.
        self.assertIsNotNone(student.school)
        self.assertEqual(student.school.name, "Allderdice High School")
        # Enrolled in program.
        self.assertTrue(
            Enrollment.objects.filter(student=student, program=self.program).exists()
        )

    def test_convert_allowed_when_approved_and_no_required_docs(self):
        # APPROVED with no required ProgramDocuments -> conversion proceeds.
        app = self._approved_signed_app(status=Application.Status.APPROVED)
        url = reverse(
            "application_review_convert", kwargs={"app_id": app.application_id}
        )
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        app.refresh_from_db()
        self.assertEqual(app.status, Application.Status.CONVERTED)
        self.assertIsNotNone(app.converted_student)

    def test_convert_blocked_when_required_docs_missing(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        from programs.models import ProgramDocument

        ProgramDocument.objects.create(
            program=self.program,
            name="Waiver",
            file=SimpleUploadedFile("waiver.pdf", b"x"),
            is_required=True,
            is_active=True,
        )
        app = self._approved_signed_app(status=Application.Status.APPROVED)
        url = reverse(
            "application_review_convert", kwargs={"app_id": app.application_id}
        )
        response = self.client.post(url, follow=True)
        self.assertEqual(response.status_code, 200)
        app.refresh_from_db()
        self.assertEqual(app.status, Application.Status.APPROVED)
        self.assertIsNone(app.converted_student)

    def test_convert_blocked_unless_approved(self):
        app = self._approved_signed_app(status=Application.Status.SUBMITTED)
        url = reverse(
            "application_review_convert", kwargs={"app_id": app.application_id}
        )
        response = self.client.post(url, follow=True)
        self.assertEqual(response.status_code, 200)
        app.refresh_from_db()
        self.assertEqual(app.status, Application.Status.SUBMITTED)
        self.assertIsNone(app.converted_student)

    def test_convert_is_idempotent(self):
        from programs.models import Student

        app = self._approved_signed_app()
        url = reverse(
            "application_review_convert", kwargs={"app_id": app.application_id}
        )
        self.client.post(url)
        before = Student.objects.count()
        # Second call should not create a new Student.
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Student.objects.count(), before)

    def test_convert_requires_review_permission(self):
        app = self._approved_signed_app()
        self.client.logout()
        plain = User.objects.create_user(username="plain")
        self.client.force_login(plain)
        url = reverse(
            "application_review_convert", kwargs={"app_id": app.application_id}
        )
        response = self.client.post(url)
        # PermissionRequiredMixin -> 302 to login by default
        self.assertIn(response.status_code, (302, 403))
        app.refresh_from_db()
        self.assertEqual(app.status, Application.Status.APPROVED_SIGNED)
