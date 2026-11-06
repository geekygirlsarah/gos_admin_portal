"""Tenant-boundary regressions for the public application wizard."""

from __future__ import annotations

import datetime
import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import Http404
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from applications.models import Application, ApplicationDocumentSubmission
from applications.services import send_lead_notification_email
from applications.tests.access_helpers import grant_applicant_document_access
from applications.views.documents import Step10DocumentsView
from organizations.models import Organization, UserOrganizationMembership
from programs.models import (
    Adult,
    AdultStudentRelationship,
    Program,
    ProgramDocument,
    Student,
)

_TMP_MEDIA = tempfile.mkdtemp(prefix="application-tenant-tests-")


@override_settings(MEDIA_ROOT=_TMP_MEDIA)
class ApplicationWizardTenantIsolationTests(TestCase):
    def setUp(self):
        self.alpha = Organization.objects.create(
            name="Application Alpha",
            slug="application-alpha",
            subdomain="application-alpha",
        )
        self.beta = Organization.objects.create(
            name="Application Beta",
            slug="application-beta",
            subdomain="application-beta",
        )
        today = timezone.localdate()
        self.alpha_program = Program.objects.create(
            organization=self.alpha,
            name="Alpha Future Program",
            start_date=today + datetime.timedelta(days=45),
            end_date=today + datetime.timedelta(days=120),
            applications_open=today,
            applications_close=today + datetime.timedelta(days=120),
            active=True,
        )
        self.beta_program = Program.objects.create(
            organization=self.beta,
            name="Beta Future Program",
            start_date=today + datetime.timedelta(days=45),
            end_date=today + datetime.timedelta(days=120),
            applications_open=today,
            applications_close=today + datetime.timedelta(days=120),
            active=True,
        )
        self.alpha_host = "application-alpha.localhost"

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_TMP_MEDIA, ignore_errors=True)

    def make_application(
        self,
        organization=None,
        program=None,
        email="applicant@example.test",
        applicant_type=Application.Type.PARENT,
        status=Application.Status.DRAFT,
        current_step=1,
        data=None,
        email_verified_at=None,
    ):
        return Application.objects.create(
            organization=organization or self.alpha,
            program=program,
            email=email,
            applicant_type=applicant_type,
            status=status,
            current_step=current_step,
            data=data or {},
            email_verified_at=email_verified_at,
        )

    def tenant_get(self, path, **kwargs):
        return self.client.get(path, HTTP_HOST=self.alpha_host, **kwargs)

    def tenant_post(self, path, data=None, **kwargs):
        return self.client.post(
            path, data=data or {}, HTTP_HOST=self.alpha_host, **kwargs
        )

    def test_welcome_programs_and_new_application_use_request_organization(self):
        response = self.tenant_get(reverse("apply_start"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            list(response.context["future_programs"]), [self.alpha_program]
        )

        response = self.tenant_post(reverse("apply_start"))

        self.assertEqual(response.status_code, 302)
        application = Application.objects.latest("created_at")
        self.assertEqual(application.organization_id, self.alpha.pk)

    def test_foreign_program_cannot_be_selected_for_local_application(self):
        application = self.make_application()

        response = self.tenant_post(
            reverse("apply_step4", kwargs={"app_id": application.application_id}),
            {"program": self.beta_program.pk},
        )

        self.assertNotEqual(response.status_code, 500)
        application.refresh_from_db()
        self.assertIsNone(application.program_id)
        if response.status_code == 200:
            self.assertEqual(
                list(response.context["future_programs"]), [self.alpha_program]
            )

    def test_foreign_application_cannot_be_resumed_or_rendered_on_another_host(self):
        application = self.make_application(
            organization=self.beta,
            email="private-beta-applicant@example.test",
            status=Application.Status.SUBMITTED,
            current_step=9,
            data={"step5-student": {"legal_first_name": "BetaPrivateName"}},
        )

        resume_response = self.tenant_post(
            reverse("apply_resume"), {"application_id": application.application_id}
        )
        self.assertFalse(
            resume_response.status_code == 302
            and reverse("apply_step9", kwargs={"app_id": application.application_id})
            in resume_response.url
        )

        submitted_response = self.tenant_get(
            reverse("apply_submitted", kwargs={"app_id": application.application_id})
        )
        self.assertNotEqual(submitted_response.status_code, 200)

    def test_foreign_duplicate_does_not_replace_local_application(self):
        local = self.make_application(email="shared-applicant@example.test")
        foreign = self.make_application(
            organization=self.beta,
            email="shared-applicant@example.test",
            current_step=5,
        )
        Application.objects.filter(pk=foreign.pk).update(
            updated_at=timezone.now() + datetime.timedelta(minutes=1)
        )

        response = self.tenant_post(
            reverse("apply_duplicate_found", kwargs={"app_id": local.application_id}),
            {"action": "resume"},
        )

        self.assertTrue(Application.objects.filter(pk=local.pk).exists())
        self.assertTrue(Application.objects.filter(pk=foreign.pk).exists())
        self.assertNotEqual(
            response.url if response.status_code == 302 else "",
            reverse("apply_step5", kwargs={"app_id": foreign.application_id}),
        )

    def test_start_over_preserves_draft_with_inconsistent_document_submission(self):
        current = self.make_application(email="shared-start-over@example.test")
        previous = self.make_application(
            email="shared-start-over@example.test",
            current_step=5,
        )
        document = ProgramDocument.objects.create(
            program=self.alpha_program,
            name="Alpha Previous Release",
            file="application-tenant-tests/alpha-previous-release.pdf",
            is_active=True,
        )
        submission = ApplicationDocumentSubmission.objects.create(
            organization=self.beta,
            application=previous,
            document=document,
            file="application-tenant-tests/private-previous-signature.pdf",
        )

        self.tenant_post(
            reverse("apply_duplicate_found", kwargs={"app_id": current.application_id}),
            {"action": "start_over"},
        )

        self.assertTrue(Application.objects.filter(pk=current.pk).exists())
        self.assertTrue(Application.objects.filter(pk=previous.pk).exists())
        self.assertTrue(
            ApplicationDocumentSubmission.objects.filter(pk=submission.pk).exists()
        )

    def test_foreign_student_is_not_used_for_prefill(self):
        Student.objects.create(
            organization=self.beta,
            legal_first_name="BetaPrivateStudent",
            last_name="Applicant",
            personal_email="returning-student@example.test",
            address="88 Beta Confidential Road",
        )
        application = self.make_application(
            email="returning-student@example.test",
            applicant_type=Application.Type.STUDENT,
            status=Application.Status.EMAIL_VERIFIED,
            current_step=5,
            email_verified_at=timezone.now(),
        )

        response = self.tenant_get(
            reverse("apply_step5", kwargs={"app_id": application.application_id})
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "BetaPrivateStudent")
        self.assertNotContains(response, "88 Beta Confidential Road")

    def test_foreign_adult_is_not_used_for_parent_prefill(self):
        Adult.objects.create(
            organization=self.beta,
            legal_first_name="BetaPrivateGuardian",
            last_name="Applicant",
            personal_email="returning-parent@example.test",
            address="99 Beta Guardian Road",
            is_parent=True,
        )
        application = self.make_application(
            email="returning-parent@example.test",
            status=Application.Status.EMAIL_VERIFIED,
            current_step=7,
            data={"step5-student": {"legal_first_name": "Local Student"}},
            email_verified_at=timezone.now(),
        )

        response = self.tenant_get(
            reverse("apply_step7", kwargs={"app_id": application.application_id})
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "BetaPrivateGuardian")
        self.assertNotContains(response, "99 Beta Guardian Road")

    def test_foreign_adult_profile_cannot_authorize_parent_handoff(self):
        user = get_user_model().objects.create_user(
            username="foreign-handoff-parent",
            email="foreign-handoff-parent@example.test",
        )
        Adult.objects.create(
            organization=self.beta,
            user=user,
            legal_first_name="Foreign Handoff",
            last_name="Parent",
            personal_email="foreign-handoff-parent@example.test",
            is_parent=True,
        )
        application = self.make_application(
            email="student-applicant@example.test",
            applicant_type=Application.Type.STUDENT,
            status=Application.Status.AWAITING_PARENT,
            current_step=5,
            data={
                "step7_handoff": {"parent_email": "foreign-handoff-parent@example.test"}
            },
            email_verified_at=timezone.now(),
        )
        application.handoff_token = "foreign-profile-handoff-token"  # nosec B105
        application.save(update_fields=["handoff_token", "updated_at"])
        request = RequestFactory().get(
            reverse("apply_continue", kwargs={"app_id": application.application_id})
        )
        request.user = user
        request.session = {}

        from applications.views.utils import _auto_authorize_handoff

        _auto_authorize_handoff(request, application)

        self.assertNotIn(f"handoff_token_{application.application_id}", request.session)

    def test_same_organization_adult_can_still_authorize_parent_handoff(self):
        user = get_user_model().objects.create_user(
            username="local-handoff-parent",
            email="local-handoff-parent@example.test",
        )
        Adult.objects.create(
            organization=self.alpha,
            user=user,
            legal_first_name="Local Handoff",
            last_name="Parent",
            personal_email="local-handoff-parent@example.test",
            is_parent=True,
        )
        application = self.make_application(
            email="local-student-applicant@example.test",
            applicant_type=Application.Type.STUDENT,
            status=Application.Status.AWAITING_PARENT,
            current_step=5,
            data={
                "step7_handoff": {"parent_email": "local-handoff-parent@example.test"}
            },
            email_verified_at=timezone.now(),
        )
        application.handoff_token = "local-profile-handoff-token"  # nosec B105
        application.save(update_fields=["handoff_token", "updated_at"])
        request = RequestFactory().get(
            reverse("apply_continue", kwargs={"app_id": application.application_id})
        )
        request.user = user
        request.session = {}

        from applications.views.utils import _auto_authorize_handoff

        _auto_authorize_handoff(request, application)

        self.assertEqual(
            request.session.get(f"handoff_token_{application.application_id}"),
            "local-profile-handoff-token",
        )

    def test_foreign_contact_link_is_not_used_for_parent_prefill(self):
        foreign_guardian = Adult.objects.create(
            organization=self.beta,
            legal_first_name="LinkedBetaGuardian",
            last_name="Applicant",
            personal_email="linked-beta-guardian@example.test",
            address="77 Linked Beta Road",
            is_parent=True,
        )
        student = Student.objects.create(
            organization=self.alpha,
            legal_first_name="Local Student",
            last_name="Applicant",
        )
        relationship = AdultStudentRelationship.objects.create(
            organization=self.alpha,
            adult=foreign_guardian,
            student=student,
        )
        student.primary_contact_relationship = relationship
        student.save()
        application = self.make_application(
            email="new-local-parent@example.test",
            status=Application.Status.EMAIL_VERIFIED,
            current_step=7,
            data={"step5-student": {"_existing_student_id": student.pk}},
            email_verified_at=timezone.now(),
        )

        response = self.tenant_get(
            reverse("apply_step7", kwargs={"app_id": application.application_id})
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "LinkedBetaGuardian")
        self.assertNotContains(response, "77 Linked Beta Road")

    def test_foreign_signed_submission_is_hidden_and_not_overwritten(self):
        application = self.make_application(
            program=self.alpha_program,
            status=Application.Status.APPROVED,
            current_step=10,
        )
        document = ProgramDocument.objects.create(
            program=self.alpha_program,
            name="Alpha Release",
            file="application-tenant-tests/alpha-release.pdf",
            is_active=True,
            is_required=True,
        )
        submission = ApplicationDocumentSubmission.objects.create(
            organization=self.beta,
            application=application,
            document=document,
            file="application-tenant-tests/private-beta-signed.pdf",
        )
        url = reverse("apply_step10", kwargs={"app_id": application.application_id})
        grant_applicant_document_access(self.client, application)

        response = self.tenant_get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["rows"][0]["submission"])

        self.tenant_post(
            url,
            {
                "document_id": document.pk,
                "file": SimpleUploadedFile("replacement.pdf", b"new signed content"),
            },
        )

        submission.refresh_from_db()
        self.assertEqual(
            submission.file.name, "application-tenant-tests/private-beta-signed.pdf"
        )
        self.assertEqual(submission.organization_id, self.beta.pk)
        application.refresh_from_db()
        self.assertEqual(application.status, Application.Status.APPROVED)

    def test_signed_submission_download_is_scoped_to_application_organization(self):
        application = self.make_application(
            program=self.alpha_program,
            status=Application.Status.APPROVED,
            current_step=10,
        )
        document = ProgramDocument.objects.create(
            program=self.alpha_program,
            name="Alpha Download Consent",
            file="application-tenant-tests/alpha-download-consent.pdf",
            is_active=True,
            is_required=True,
        )
        submission = ApplicationDocumentSubmission.objects.create(
            organization=self.alpha,
            application=application,
            document=document,
        )
        submission.file.save("signed-consent.pdf", ContentFile(b"private signed file"))
        grant_applicant_document_access(self.client, application)
        url = reverse(
            "apply_step10_document_download",
            kwargs={"app_id": application.application_id, "document_id": document.pk},
        )

        response = self.tenant_get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"private signed file")

        foreign_host_response = self.client.get(
            url, HTTP_HOST="application-beta.localhost"
        )
        self.assertNotEqual(foreign_host_response.status_code, 200)

    def test_new_signed_submission_is_owned_by_application_organization(self):
        application = self.make_application(
            program=self.alpha_program,
            status=Application.Status.APPROVED,
            current_step=10,
        )
        grant_applicant_document_access(self.client, application)
        document = ProgramDocument.objects.create(
            program=self.alpha_program,
            name="Alpha Consent",
            file="application-tenant-tests/alpha-consent.pdf",
            is_active=True,
            is_required=True,
        )

        response = self.tenant_post(
            reverse("apply_step10", kwargs={"app_id": application.application_id}),
            {
                "document_id": document.pk,
                "file": SimpleUploadedFile("signed.pdf", b"signed content"),
            },
        )

        self.assertEqual(response.status_code, 302)
        submission = ApplicationDocumentSubmission.objects.get(
            application=application, document=document
        )
        self.assertEqual(submission.organization_id, self.alpha.pk)
        application.refresh_from_db()
        self.assertEqual(application.status, Application.Status.APPROVED_SIGNED)

    def test_foreign_program_document_cannot_be_uploaded_to_local_application(self):
        application = self.make_application(
            program=self.beta_program,
            status=Application.Status.APPROVED,
            current_step=10,
        )
        document = ProgramDocument.objects.create(
            program=self.beta_program,
            name="Beta Private Consent",
            file="application-tenant-tests/beta-consent.pdf",
            is_active=True,
            is_required=True,
        )

        response = self.tenant_get(
            reverse("apply_step10", kwargs={"app_id": application.application_id})
        )

        self.assertNotEqual(response.status_code, 200)

    @override_settings(
        MAILERS={
            "default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}
        }
    )
    def test_lead_notification_only_reaches_active_leads_in_application_organization(
        self,
    ):
        application = self.make_application(
            email="applicant-with-lead-notice@example.test",
            status=Application.Status.SUBMITTED,
            data={"step5-student": {"legal_first_name": "Applicant"}},
        )
        user_model = get_user_model()
        alpha_lead = user_model.objects.create_user(
            username="alpha-application-lead",
            email="alpha-lead@example.test",
        )
        beta_lead = user_model.objects.create_user(
            username="beta-application-lead",
            email="beta-lead@example.test",
        )
        inactive_alpha_lead = user_model.objects.create_user(
            username="inactive-alpha-application-lead",
            email="inactive-alpha-lead@example.test",
        )
        UserOrganizationMembership.objects.create(
            user=alpha_lead,
            organization=self.alpha,
            role="LeadMentor",
            is_active=True,
        )
        UserOrganizationMembership.objects.create(
            user=beta_lead,
            organization=self.beta,
            role="LeadMentor",
            is_active=True,
        )
        UserOrganizationMembership.objects.create(
            user=inactive_alpha_lead,
            organization=self.alpha,
            role="LeadMentor",
            is_active=False,
        )
        mail.outbox = []

        send_lead_notification_email(application)

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["alpha-lead@example.test"])

    @override_settings(
        MAILERS={
            "default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}
        }
    )
    def test_lead_notification_is_not_sent_without_active_tenant_leads(self):
        application = self.make_application(
            email="applicant-without-local-lead@example.test",
            status=Application.Status.SUBMITTED,
        )
        foreign_lead = get_user_model().objects.create_user(
            username="only-foreign-application-lead",
            email="foreign-lead@example.test",
        )
        UserOrganizationMembership.objects.create(
            user=foreign_lead,
            organization=self.beta,
            role="LeadMentor",
            is_active=True,
        )
        mail.outbox = []

        send_lead_notification_email(application)

        self.assertEqual(mail.outbox, [])

    def test_missing_organization_context_fails_closed(self):
        application = self.make_application(status=Application.Status.APPROVED)
        request = RequestFactory().get(
            reverse("apply_step10", kwargs={"app_id": application.application_id})
        )
        request.organization = None

        with self.assertRaises(Http404):
            Step10DocumentsView.as_view()(request, app_id=application.application_id)
