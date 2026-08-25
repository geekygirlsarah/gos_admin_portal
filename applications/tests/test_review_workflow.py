"""Tests for the lead-mentor application review workflow: permissions,
approve/decline/edit/delete, convert, and email resend."""

from __future__ import annotations

import datetime

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from applications.models import Application
from programs.models import Program, ProgramDocument

User = get_user_model()

LEAD_MENTORS_GROUP = "LeadMentor"
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
    """After bootstrap, the LeadMentor group exists with the review perm."""

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
        self.assertGreaterEqual(self.app.current_step, 9)
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        recipients = {r.lower() for r in msg.to}
        self.assertIn("parent@example.com", recipients)
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

    def test_cannot_decline_already_declined(self):
        """Re-declining a DECLINED application should be blocked."""
        self.app.status = Application.Status.DECLINED
        self.app.save()
        mail.outbox = []
        url = reverse(
            "application_review_decline", kwargs={"app_id": self.app.application_id}
        )
        response = self.client.post(url, {"reason": "Trying again."})
        self.assertEqual(response.status_code, 302)
        self.app.refresh_from_db()
        self.assertEqual(self.app.status, Application.Status.DECLINED)
        self.assertNotEqual(self.app.decline_reason, "Trying again.")
        self.assertEqual(len(mail.outbox), 0)

    def test_cannot_approve_declined_application(self):
        """Approving a DECLINED application should be blocked."""
        self.app.status = Application.Status.DECLINED
        self.app.save()
        mail.outbox = []
        url = reverse(
            "application_review_approve", kwargs={"app_id": self.app.application_id}
        )
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.app.refresh_from_db()
        self.assertEqual(self.app.status, Application.Status.DECLINED)
        self.assertEqual(len(mail.outbox), 0)

    def test_decline_get_redirects_when_already_declined(self):
        """GET on the decline form for a DECLINED app redirects to detail."""
        self.app.status = Application.Status.DECLINED
        self.app.save()
        url = reverse(
            "application_review_decline", kwargs={"app_id": self.app.application_id}
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(f"/apply/review/{self.app.application_id}/", response.url)

    def test_detail_shows_disabled_buttons_for_declined(self):
        """Detail page for a DECLINED app should disable Approve and Decline."""
        self.app.status = Application.Status.DECLINED
        self.app.save()
        url = reverse(
            "application_review_detail", kwargs={"app_id": self.app.application_id}
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        # The decline link should not be present (replaced by disabled button)
        decline_url = reverse(
            "application_review_decline", kwargs={"app_id": self.app.application_id}
        )
        self.assertNotContains(response, decline_url)

    def test_edit_saves_fields_and_email(self):
        url = reverse(
            "application_review_edit", kwargs={"app_id": self.app.application_id}
        )
        response = self.client.post(
            url,
            {
                "email": "new-parent@example.com",
                "step5-student__legal_first_name": "Grace",
                "step5-student__last_name": "Hopper",
                "step5-student__date_of_birth": "2005-03-14",
                "step5-student__school_name": "Test High",
                "step5-student__grade": "12",
                "step7-primaryparent__first_name": "Pat",
                "step7-primaryparent__last_name": "Parent",
                "step7-primaryparent__email": "parent@example.com",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.app.refresh_from_db()
        self.assertEqual(self.app.email, "new-parent@example.com")
        step5 = self.app.data["step5-student"]
        self.assertEqual(step5["legal_first_name"], "Grace")
        self.assertEqual(step5["last_name"], "Hopper")
        # Non-spec fields stored previously are preserved.
        self.assertEqual(step5["email"], "ada@example.com")
        self.assertEqual(self.app.data["step7-primaryparent"]["first_name"], "Pat")

    def test_edit_get_renders_step_fields(self):
        url = reverse(
            "application_review_edit", kwargs={"app_id": self.app.application_id}
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Legal first name")
        self.assertContains(response, "Student experience")
        self.assertContains(response, "Primary parent / guardian")

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
                    "phone_number": "555-444-0100",
                    "phone_type": "cell",
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
        self.assertIsNotNone(student.primary_contact)
        self.assertEqual(student.primary_contact.personal_email, "parent@example.com")
        self.assertIsNotNone(student.secondary_contact)
        self.assertEqual(student.secondary_contact.personal_email, "sam@example.com")
        self.assertIsNotNone(student.school)
        self.assertEqual(student.school.name, "Allderdice High School")
        self.assertTrue(
            Enrollment.objects.filter(student=student, program=self.program).exists()
        )

    def test_convert_allowed_when_approved_and_no_required_docs(self):
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
        self.assertIn(response.status_code, (302, 403))
        app.refresh_from_db()
        self.assertEqual(app.status, Application.Status.APPROVED_SIGNED)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class LeadMentorGroupMergeTests(TestCase):
    """After the group merge, the single 'LeadMentor' group grants
    access to the application review pages."""

    def setUp(self):
        self.app = _make_application()
        self.list_url = reverse("application_review_list")
        self.detail_url = reverse(
            "application_review_detail",
            kwargs={"app_id": self.app.application_id},
        )
        self.lead_mentor_group, _ = Group.objects.get_or_create(name=LEAD_MENTORS_GROUP)
        self.lead_mentor_group.permissions.add(_ensure_review_perm())

    def test_lead_mentor_group_has_review_permission(self):
        perm_codenames = set(
            self.lead_mentor_group.permissions.values_list("codename", flat=True)
        )
        self.assertIn(
            REVIEW_PERM_CODENAME,
            perm_codenames,
            "The 'LeadMentor' group is missing the 'review_application' permission.",
        )

    def test_lead_mentor_group_member_can_access_review_list(self):
        user = User.objects.create_user(username="lm_user", email="lm@x.test")
        user.groups.add(self.lead_mentor_group)
        self.client.force_login(user)
        response = self.client.get(self.list_url)
        self.assertEqual(
            response.status_code,
            200,
            "Expected LeadMentor group member to access review list (got "
            f"{response.status_code}).",
        )

    def test_lead_mentor_group_member_can_access_review_detail(self):
        user = User.objects.create_user(username="lm_user2", email="lm2@x.test")
        user.groups.add(self.lead_mentor_group)
        self.client.force_login(user)
        response = self.client.get(self.detail_url)
        self.assertEqual(
            response.status_code,
            200,
            "Expected LeadMentor group member to access review detail (got "
            f"{response.status_code}).",
        )

    def test_old_lead_mentors_group_no_longer_exists(self):
        self.assertFalse(
            Group.objects.filter(name="Lead Mentors").exists(),
            "The deprecated 'Lead Mentors' group still exists; "
            "it should have been renamed to 'LeadMentor'.",
        )

    def test_user_without_group_cannot_access_review(self):
        plain = User.objects.create_user(username="plain_user", email="plain@x.test")
        self.client.force_login(plain)
        response = self.client.get(self.list_url)
        self.assertIn(
            response.status_code,
            [302, 403],
            "Plain user should not be able to access review pages.",
        )


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
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("verification code", mail.outbox[0].subject.lower())
        self.assertIn("parent@example.com", mail.outbox[0].to)

    def test_resend_handoff_email(self):
        self.app.status = Application.Status.AWAITING_PARENT
        self.app.save()
        url = reverse(
            "application_review_resend_email",
            kwargs={"app_id": self.app.application_id},
        )
        response = self.client.post(url, {"type": "handoff"})
        self.assertEqual(response.status_code, 302)
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

    def test_resend_handoff_email_student_initiated(self):
        self.app.applicant_type = Application.Type.STUDENT
        self.app.email = "student@example.com"
        self.app.data = {"step7_handoff": {"parent_email": "real-parent@example.com"}}
        self.app.status = Application.Status.AWAITING_PARENT
        self.app.save()
        url = reverse(
            "application_review_resend_email",
            kwargs={"app_id": self.app.application_id},
        )
        response = self.client.post(url, {"type": "handoff"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("real-parent@example.com", mail.outbox[0].to)

    def test_invalid_type_error(self):
        url = reverse(
            "application_review_resend_email",
            kwargs={"app_id": self.app.application_id},
        )
        response = self.client.post(url, {"type": "invalid"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 0)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class StaffDocumentUploadTests(TestCase):
    """Lead mentors can upload signed documents on behalf of applicants
    (e.g. paper copies received in person)."""

    def setUp(self):
        import datetime

        self.program = Program.objects.create(
            name="Robotics 2099",
            start_date=datetime.date(2099, 1, 1),
            end_date=datetime.date(2099, 6, 30),
            active=True,
        )
        self.user = _reviewer_user()
        self.client.force_login(self.user)
        self.app = _make_application(
            program=self.program,
            status=Application.Status.APPROVED,
            current_step=9,
        )
        self.doc = ProgramDocument.objects.create(
            program=self.program,
            name="Photo Release",
            file="blank_photo_release.pdf",
            is_required=True,
            is_active=True,
        )
        self.url = reverse(
            "application_review_upload_document",
            kwargs={"app_id": self.app.application_id},
        )

    def test_get_returns_405(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 405)

    def test_upload_document_creates_submission(self):
        from applications.models import ApplicationDocumentSubmission

        upload = SimpleUploadedFile(
            "signed_release.pdf", b"signed-content", content_type="application/pdf"
        )
        response = self.client.post(self.url, {"document": self.doc.pk, "file": upload})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            ApplicationDocumentSubmission.objects.filter(
                application=self.app, document=self.doc
            ).exists()
        )

    def test_upload_promotes_approved_to_approved_signed(self):
        from applications.models import ApplicationDocumentSubmission

        self.assertEqual(self.app.status, Application.Status.APPROVED)
        upload = SimpleUploadedFile(
            "signed_release.pdf", b"signed-content", content_type="application/pdf"
        )
        self.client.post(self.url, {"document": self.doc.pk, "file": upload})
        self.app.refresh_from_db()
        self.assertEqual(self.app.status, Application.Status.APPROVED_SIGNED)

    def test_upload_does_not_promote_if_other_required_docs_missing(self):
        from applications.models import ApplicationDocumentSubmission

        ProgramDocument.objects.create(
            program=self.program,
            name="Medical Form",
            file="blank_medical.pdf",
            is_required=True,
            is_active=True,
        )
        upload = SimpleUploadedFile(
            "signed_release.pdf", b"signed-content", content_type="application/pdf"
        )
        self.client.post(self.url, {"document": self.doc.pk, "file": upload})
        self.app.refresh_from_db()
        self.assertEqual(self.app.status, Application.Status.APPROVED)

    def test_upload_replaces_existing_submission(self):
        from applications.models import ApplicationDocumentSubmission

        ApplicationDocumentSubmission.objects.create(
            application=self.app,
            document=self.doc,
            file=SimpleUploadedFile(
                "old_signed.pdf", b"old-content", content_type="application/pdf"
            ),
        )
        upload = SimpleUploadedFile(
            "new_signed.pdf", b"new-content", content_type="application/pdf"
        )
        response = self.client.post(self.url, {"document": self.doc.pk, "file": upload})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            ApplicationDocumentSubmission.objects.filter(
                application=self.app, document=self.doc
            ).count(),
            1,
        )

    def test_upload_requires_review_permission(self):
        self.client.logout()
        plain = User.objects.create_user(username="plain_upload")
        self.client.force_login(plain)
        upload = SimpleUploadedFile(
            "signed_release.pdf", b"signed-content", content_type="application/pdf"
        )
        response = self.client.post(self.url, {"document": self.doc.pk, "file": upload})
        self.assertIn(response.status_code, (302, 403))

    def test_upload_rejects_invalid_document(self):
        from applications.models import ApplicationDocumentSubmission

        upload = SimpleUploadedFile(
            "signed_release.pdf", b"signed-content", content_type="application/pdf"
        )
        response = self.client.post(self.url, {"document": 99999, "file": upload})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            ApplicationDocumentSubmission.objects.filter(application=self.app).exists()
        )

    def test_upload_rejects_missing_file(self):
        response = self.client.post(self.url, {"document": self.doc.pk})
        self.assertEqual(response.status_code, 302)
        # Still redirects (error via messages), no submission created
        from applications.models import ApplicationDocumentSubmission

        self.assertFalse(
            ApplicationDocumentSubmission.objects.filter(application=self.app).exists()
        )

    def test_upload_redirects_back_to_detail(self):
        upload = SimpleUploadedFile(
            "signed_release.pdf", b"signed-content", content_type="application/pdf"
        )
        response = self.client.post(self.url, {"document": self.doc.pk, "file": upload})
        self.assertEqual(response.status_code, 302)
        self.assertIn(
            reverse(
                "application_review_detail",
                kwargs={"app_id": self.app.application_id},
            ),
            response.url,
        )
