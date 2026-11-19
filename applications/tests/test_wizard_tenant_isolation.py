"""Tenant-boundary regressions for the public application wizard."""

from __future__ import annotations

import datetime
import shutil
import tempfile
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import Http404
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from applications.forms import (
    ApplicantTypeForm,
    ConfirmSubmitForm,
    ParentHandoffForm,
    ParentInfoForm,
    ProgramSelectForm,
    StudentExperienceForm,
)
from applications.models import (
    Application,
    ApplicationDocumentSubmission,
    ProgramApplicationQuestion,
)
from applications.services import send_lead_notification_email
from applications.tests.access_helpers import grant_applicant_document_access
from applications.views.documents import Step10DocumentsView
from applications.views.steps_student import _locked_application_queryset
from organizations.models import Organization, UserOrganizationMembership
from programs.models import (
    Adult,
    AdultStudentRelationship,
    Program,
    ProgramDocument,
    Student,
)

_TMP_MEDIA = tempfile.mkdtemp(prefix="application-tenant-tests-")


def _parent_info_payload(email):
    return {
        "legal_first_name": "Pat",
        "last_name": "Parent",
        "relationship_to_student": "parent",
        "email": email,
        "address": "123 Main St",
        "city": "Pittsburgh",
        "state": "PA",
        "zip_code": "15213",
        "phone_number": "555-444-1212",
        "phone_type": "cell",
        "email_updates": "on",
    }


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

    def verify_application_browser(self, application, expected_recipient=None):
        access_url = reverse(
            "apply_browser_verification",
            kwargs={"app_id": application.application_id},
        )
        with patch(
            "applications.views.browser.generate_otp_code", return_value="123456"
        ):
            with patch("applications.views.browser.send_otp_email") as send_otp_email:
                self.tenant_post(access_url, {"action": "send"})
        if expected_recipient is not None:
            self.assertEqual(
                send_otp_email.call_args.kwargs["recipient"], expected_recipient
            )
        response = self.tenant_post(access_url, {"code": "123456"})
        self.assertEqual(response.status_code, 302)

    def make_linked_family(self, student_email, primary_email, secondary_email):
        student = Student.objects.create(
            organization=self.alpha,
            legal_first_name="Existing Student",
            last_name="Applicant",
            personal_email=student_email,
            address="Private Student Street",
            city="Pittsburgh",
            state="PA",
            zip_code="15213",
        )
        primary = Adult.objects.create(
            organization=self.alpha,
            legal_first_name="Private Primary Guardian",
            last_name="Applicant",
            personal_email=primary_email,
            address="Private Guardian Street",
            is_parent=True,
        )
        secondary = Adult.objects.create(
            organization=self.alpha,
            legal_first_name="Private Secondary Guardian",
            last_name="Applicant",
            personal_email=secondary_email,
            is_parent=True,
        )
        primary_relationship = AdultStudentRelationship.objects.create(
            organization=self.alpha,
            adult=primary,
            student=student,
        )
        secondary_relationship = AdultStudentRelationship.objects.create(
            organization=self.alpha,
            adult=secondary,
            student=student,
        )
        student.primary_contact_relationship = primary_relationship
        student.secondary_contact_relationship = secondary_relationship
        student.save(
            update_fields=[
                "primary_contact_relationship",
                "secondary_contact_relationship",
            ]
        )
        return student, primary, secondary

    def patch_validation_after_intervening_write(self, form_class, update):
        original_is_valid = form_class.is_valid
        updated = False

        def is_valid(form):
            nonlocal updated
            valid = original_is_valid(form)
            if valid and not updated:
                updated = True
                update()
            return valid

        return patch.object(form_class, "is_valid", new=is_valid)

    def test_locked_application_query_avoids_nullable_program_join(self):
        for program in (None, self.alpha_program):
            with self.subTest(program=program):
                application = self.make_application(
                    program=program,
                    status=Application.Status.EMAIL_VERIFIED,
                    email_verified_at=timezone.now(),
                )
                queryset = _locked_application_queryset(
                    application._state.db,
                    application.pk,
                    application.organization_id,
                )

                self.assertTrue(queryset.query.select_for_update)
                self.assertNotIn(Program._meta.db_table, str(queryset.query))
                self.assertEqual(queryset.get().pk, application.pk)

        foreign_application = self.make_application(
            organization=self.beta, program=self.beta_program
        )
        foreign_queryset = _locked_application_queryset(
            foreign_application._state.db,
            foreign_application.pk,
            self.alpha.pk,
        )
        self.assertFalse(foreign_queryset.exists())

    def test_submission_rechecks_current_parent_notification_opt_in(self):
        application = self.make_application(
            email="submit-opt-in@example.test",
            program=self.alpha_program,
            status=Application.Status.EMAIL_VERIFIED,
            current_step=9,
            data={"step7-primaryparent": {"email_updates": True}},
            email_verified_at=timezone.now(),
        )
        self.verify_application_browser(application)
        mail.outbox = []

        def remove_latest_opt_in():
            latest = Application.objects.get(pk=application.pk)
            latest.data["step7-primaryparent"]["email_updates"] = False
            latest.save(update_fields=["data", "updated_at"])

        with self.patch_validation_after_intervening_write(
            ConfirmSubmitForm, remove_latest_opt_in
        ):
            response = self.tenant_post(
                reverse("apply_step9", kwargs={"app_id": application.application_id}),
                {"confirm": "on"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "At least one parent or guardian must opt in")
        application.refresh_from_db()
        self.assertEqual(application.status, Application.Status.EMAIL_VERIFIED)
        self.assertFalse(application.data["step7-primaryparent"]["email_updates"])
        self.assertEqual(mail.outbox, [])

    def test_step_write_does_not_update_application_submitted_during_request(self):
        application = self.make_application(
            email="step6-submitted@example.test",
            status=Application.Status.EMAIL_VERIFIED,
            current_step=6,
            data={"step6-experience": {"prior_robotics_experience": "Earlier"}},
            email_verified_at=timezone.now(),
        )
        self.verify_application_browser(application)
        step_url = reverse("apply_step6", kwargs={"app_id": application.application_id})
        get_response = self.tenant_get(step_url)
        version = get_response.context["form"].initial["experience_version"]

        def submit_latest_application():
            Application.objects.filter(pk=application.pk).update(
                status=Application.Status.SUBMITTED
            )

        with self.patch_validation_after_intervening_write(
            StudentExperienceForm, submit_latest_application
        ):
            response = self.tenant_post(
                step_url,
                {
                    "experience_version": version,
                    "interest_reason": "Robotics",
                    "hoped_gains": "Experience",
                    "prior_robotics_experience": "Current submission",
                    "referral_source": "Friend",
                },
            )

        self.assertRedirects(
            response,
            reverse("apply_submitted", kwargs={"app_id": application.application_id}),
            fetch_redirect_response=False,
        )
        application.refresh_from_db()
        self.assertEqual(application.status, Application.Status.SUBMITTED)
        self.assertEqual(
            application.data["step6-experience"]["prior_robotics_experience"],
            "Earlier",
        )

    def test_primary_parent_write_rechecks_secondary_email_under_lock(self):
        application = self.make_application(
            email="primary-email-race@example.test",
            status=Application.Status.EMAIL_VERIFIED,
            current_step=7,
            data={"step8-secondaryparent": {"email": "other@example.test"}},
            email_verified_at=timezone.now(),
        )
        self.verify_application_browser(application)

        def claim_primary_email_for_secondary():
            latest = Application.objects.get(pk=application.pk)
            latest.data["step8-secondaryparent"]["email"] = "shared@example.test"
            latest.save(update_fields=["data", "updated_at"])

        with self.patch_validation_after_intervening_write(
            ParentInfoForm, claim_primary_email_for_secondary
        ):
            response = self.tenant_post(
                reverse("apply_step7", kwargs={"app_id": application.application_id}),
                _parent_info_payload("shared@example.test"),
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("email", response.context["form"].errors)
        application.refresh_from_db()
        self.assertNotIn("step7-primaryparent", application.data)
        self.assertEqual(
            application.data["step8-secondaryparent"]["email"], "shared@example.test"
        )

    def test_parent_handoff_rechecks_secondary_email_under_lock(self):
        application = self.make_application(
            email="student-handoff-race@example.test",
            applicant_type=Application.Type.STUDENT,
            status=Application.Status.EMAIL_VERIFIED,
            current_step=7,
            data={"step8-secondaryparent": {"email": "other@example.test"}},
            email_verified_at=timezone.now(),
        )
        self.verify_application_browser(application)

        def claim_handoff_email_for_secondary():
            latest = Application.objects.get(pk=application.pk)
            latest.data["step8-secondaryparent"]["email"] = "shared@example.test"
            latest.save(update_fields=["data", "updated_at"])

        with self.patch_validation_after_intervening_write(
            ParentHandoffForm, claim_handoff_email_for_secondary
        ):
            response = self.tenant_post(
                reverse("apply_step7", kwargs={"app_id": application.application_id}),
                {"parent_email": "shared@example.test"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("parent_email", response.context["handoff_form"].errors)
        application.refresh_from_db()
        self.assertEqual(application.status, Application.Status.EMAIL_VERIFIED)
        self.assertNotIn("step7_handoff", application.data)

    def test_secondary_parent_write_rechecks_primary_email_under_lock(self):
        application = self.make_application(
            email="secondary-email-race@example.test",
            status=Application.Status.EMAIL_VERIFIED,
            current_step=8,
            data={
                "step7-primaryparent": {
                    "email": "other@example.test",
                    "email_updates": True,
                }
            },
            email_verified_at=timezone.now(),
        )
        self.verify_application_browser(application)

        def claim_secondary_email_for_primary():
            latest = Application.objects.get(pk=application.pk)
            latest.data["step7-primaryparent"]["email"] = "shared@example.test"
            latest.save(update_fields=["data", "updated_at"])

        with self.patch_validation_after_intervening_write(
            ParentInfoForm, claim_secondary_email_for_primary
        ):
            response = self.tenant_post(
                reverse("apply_step8", kwargs={"app_id": application.application_id}),
                _parent_info_payload("shared@example.test"),
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("email", response.context["form"].errors)
        application.refresh_from_db()
        self.assertNotIn("step8-secondaryparent", application.data)
        self.assertEqual(
            application.data["step7-primaryparent"]["email"], "shared@example.test"
        )

    def test_step_write_rejects_program_change_during_request(self):
        application = self.make_application(
            email="step6-program-change@example.test",
            program=self.alpha_program,
            status=Application.Status.EMAIL_VERIFIED,
            current_step=6,
            data={"step6-experience": {"prior_robotics_experience": "Earlier"}},
            email_verified_at=timezone.now(),
        )
        self.verify_application_browser(application)
        step_url = reverse("apply_step6", kwargs={"app_id": application.application_id})
        get_response = self.tenant_get(step_url)
        version = get_response.context["form"].initial["experience_version"]

        def clear_latest_program():
            Application.objects.filter(pk=application.pk).update(program=None)

        with self.patch_validation_after_intervening_write(
            StudentExperienceForm, clear_latest_program
        ):
            response = self.tenant_post(
                step_url,
                {
                    "experience_version": version,
                    "interest_reason": "Robotics",
                    "hoped_gains": "Experience",
                    "prior_robotics_experience": "Current submission",
                    "referral_source": "Friend",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "selected program changed")
        application.refresh_from_db()
        self.assertIsNone(application.program_id)
        self.assertEqual(
            application.data["step6-experience"]["prior_robotics_experience"],
            "Earlier",
        )

    def test_secondary_parent_write_rejects_changed_student_email_context(self):
        submitted_parent_email = "new-student@example.test"
        application = self.make_application(
            email="student-applicant@example.test",
            applicant_type=Application.Type.STUDENT,
            status=Application.Status.EMAIL_VERIFIED,
            current_step=8,
            data={
                "step5-student": {"personal_email": "old-student@example.test"},
                "step7-primaryparent": {"email_updates": True},
            },
            email_verified_at=timezone.now(),
        )
        self.verify_application_browser(application)

        def change_student_email_context():
            latest = Application.objects.get(pk=application.pk)
            latest.data["step5-student"]["personal_email"] = submitted_parent_email
            latest.save(update_fields=["data", "updated_at"])

        with self.patch_validation_after_intervening_write(
            ParentInfoForm, change_student_email_context
        ):
            response = self.tenant_post(
                reverse("apply_step8", kwargs={"app_id": application.application_id}),
                {
                    "legal_first_name": "Secondary",
                    "last_name": "Parent",
                    "relationship_to_student": "parent",
                    "email": submitted_parent_email,
                    "phone_number": "412-555-1212",
                    "phone_type": "cell",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, submitted_parent_email)
        application.refresh_from_db()
        self.assertNotIn("step8-secondaryparent", application.data)

    def test_secondary_parent_write_rechecks_latest_primary_opt_in(self):
        application = self.make_application(
            email="primary-opt-in-change@example.test",
            status=Application.Status.EMAIL_VERIFIED,
            current_step=8,
            data={"step7-primaryparent": {"email_updates": True}},
            email_verified_at=timezone.now(),
        )
        self.verify_application_browser(application)

        def remove_primary_opt_in():
            latest = Application.objects.get(pk=application.pk)
            latest.data["step7-primaryparent"]["email_updates"] = False
            latest.save(update_fields=["data", "updated_at"])

        with self.patch_validation_after_intervening_write(
            ParentInfoForm, remove_primary_opt_in
        ):
            response = self.tenant_post(
                reverse("apply_step8", kwargs={"app_id": application.application_id}),
                {
                    "legal_first_name": "Secondary",
                    "last_name": "Parent",
                    "relationship_to_student": "parent",
                    "email": "secondary@example.test",
                    "phone_number": "412-555-1212",
                    "phone_type": "cell",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "At least one adult contact must opt in")
        application.refresh_from_db()
        self.assertFalse(application.data["step7-primaryparent"]["email_updates"])
        self.assertNotIn("step8-secondaryparent", application.data)

    def test_submission_rejects_identity_change_after_form_validation(self):
        application = self.make_application(
            email="submit-original@example.test",
            status=Application.Status.EMAIL_VERIFIED,
            current_step=9,
            data={"step7-primaryparent": {"email_updates": True}},
            email_verified_at=timezone.now(),
        )
        self.verify_application_browser(application)
        mail.outbox = []

        def change_latest_identity():
            Application.objects.filter(pk=application.pk).update(
                email="submit-current@example.test",
                email_verified_at=None,
            )

        with self.patch_validation_after_intervening_write(
            ConfirmSubmitForm, change_latest_identity
        ):
            response = self.tenant_post(
                reverse("apply_step9", kwargs={"app_id": application.application_id}),
                {"confirm": "on"},
            )

        self.assertRedirects(
            response,
            reverse(
                "apply_browser_verification",
                kwargs={"app_id": application.application_id},
            ),
            fetch_redirect_response=False,
        )
        application.refresh_from_db()
        self.assertEqual(application.email, "submit-current@example.test")
        self.assertNotEqual(application.status, Application.Status.SUBMITTED)
        self.assertEqual(mail.outbox, [])

    def test_submission_rechecks_parent_handoff_after_form_validation(self):
        application = self.make_application(
            email="submit-student@example.test",
            applicant_type=Application.Type.STUDENT,
            status=Application.Status.EMAIL_VERIFIED,
            current_step=9,
            data={"step7-primaryparent": {"email_updates": True}},
            email_verified_at=timezone.now(),
        )
        self.verify_application_browser(application)
        mail.outbox = []

        def start_latest_handoff():
            latest = Application.objects.get(pk=application.pk)
            latest.status = Application.Status.AWAITING_PARENT
            latest.handoff_token = "current-handoff-token"  # nosec B105
            latest.data["step7_handoff"] = {
                "parent_email": "current-parent@example.test"
            }
            latest.save(update_fields=["status", "handoff_token", "data", "updated_at"])

        with self.patch_validation_after_intervening_write(
            ConfirmSubmitForm, start_latest_handoff
        ):
            response = self.tenant_post(
                reverse("apply_step9", kwargs={"app_id": application.application_id}),
                {"confirm": "on"},
            )

        self.assertRedirects(
            response,
            reverse(
                "apply_browser_verification",
                kwargs={"app_id": application.application_id},
            ),
            fetch_redirect_response=False,
        )
        application.refresh_from_db()
        self.assertEqual(application.status, Application.Status.AWAITING_PARENT)
        self.assertEqual(application.handoff_token, "current-handoff-token")
        self.assertEqual(mail.outbox, [])

    def test_submission_rejects_program_change_after_form_validation(self):
        application = self.make_application(
            email="submit-program@example.test",
            program=self.alpha_program,
            status=Application.Status.EMAIL_VERIFIED,
            current_step=9,
            data={"step7-primaryparent": {"email_updates": True}},
            email_verified_at=timezone.now(),
        )
        self.verify_application_browser(application)
        mail.outbox = []

        def change_latest_program():
            Application.objects.filter(pk=application.pk).update(program=None)

        with self.patch_validation_after_intervening_write(
            ConfirmSubmitForm, change_latest_program
        ):
            response = self.tenant_post(
                reverse("apply_step9", kwargs={"app_id": application.application_id}),
                {"confirm": "on"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "program changed")
        application.refresh_from_db()
        self.assertIsNone(application.program_id)
        self.assertEqual(application.status, Application.Status.EMAIL_VERIFIED)
        self.assertEqual(mail.outbox, [])

    def test_submission_does_not_repeat_notifications_after_concurrent_submit(self):
        application = self.make_application(
            email="submit-status@example.test",
            status=Application.Status.EMAIL_VERIFIED,
            current_step=9,
            data={"step7-primaryparent": {"email_updates": True}},
            email_verified_at=timezone.now(),
        )
        self.verify_application_browser(application)
        already_submitted_at = timezone.now() - datetime.timedelta(minutes=1)
        mail.outbox = []

        def submit_latest_application():
            Application.objects.filter(pk=application.pk).update(
                status=Application.Status.SUBMITTED,
                submitted_at=already_submitted_at,
            )

        with self.patch_validation_after_intervening_write(
            ConfirmSubmitForm, submit_latest_application
        ):
            response = self.tenant_post(
                reverse("apply_step9", kwargs={"app_id": application.application_id}),
                {"confirm": "on"},
            )

        self.assertRedirects(
            response,
            reverse("apply_submitted", kwargs={"app_id": application.application_id}),
            fetch_redirect_response=False,
        )
        application.refresh_from_db()
        self.assertEqual(application.status, Application.Status.SUBMITTED)
        self.assertEqual(application.submitted_at, already_submitted_at)
        self.assertEqual(mail.outbox, [])

    def test_step2_preserves_newer_answers_and_progress(self):
        application = self.make_application(
            email="step2-merge@example.test",
            applicant_type=Application.Type.PARENT,
            status=Application.Status.EMAIL_VERIFIED,
            current_step=2,
            data={"existing": "before"},
            email_verified_at=timezone.now(),
        )
        self.verify_application_browser(application)

        def update_latest_application():
            latest = Application.objects.get(pk=application.pk)
            latest.data["step6-custom"] = [
                {"question_text": "Latest question?", "answer": "Latest answer"}
            ]
            latest.current_step = 8
            latest.save(update_fields=["data", "current_step", "updated_at"])

        with self.patch_validation_after_intervening_write(
            ApplicantTypeForm, update_latest_application
        ):
            response = self.tenant_post(
                reverse("apply_step2", kwargs={"app_id": application.application_id}),
                {
                    "applicant_type": Application.Type.STUDENT,
                    "email": application.email,
                },
            )

        self.assertEqual(response.status_code, 302)
        application.refresh_from_db()
        self.assertEqual(application.applicant_type, Application.Type.STUDENT)
        self.assertEqual(application.current_step, 8)
        self.assertEqual(application.data["existing"], "before")
        self.assertEqual(application.data["step6-custom"][0]["answer"], "Latest answer")

    def test_step2_rejects_stale_request_after_identity_changes(self):
        application = self.make_application(
            email="step2-original@example.test",
            applicant_type=Application.Type.PARENT,
            status=Application.Status.EMAIL_VERIFIED,
            current_step=2,
            email_verified_at=timezone.now(),
        )
        self.verify_application_browser(application)

        def change_latest_identity():
            latest = Application.objects.get(pk=application.pk)
            latest.email = "step2-current@example.test"
            latest.email_verified_at = None
            latest.save(update_fields=["email", "email_verified_at", "updated_at"])

        with self.patch_validation_after_intervening_write(
            ApplicantTypeForm, change_latest_identity
        ):
            response = self.tenant_post(
                reverse("apply_step2", kwargs={"app_id": application.application_id}),
                {
                    "applicant_type": Application.Type.STUDENT,
                    "email": "step2-original@example.test",
                },
            )

        self.assertRedirects(
            response,
            reverse(
                "apply_browser_verification",
                kwargs={"app_id": application.application_id},
            ),
            fetch_redirect_response=False,
        )
        application.refresh_from_db()
        self.assertEqual(application.applicant_type, Application.Type.PARENT)
        self.assertEqual(application.email, "step2-current@example.test")
        self.assertIsNone(application.email_verified_at)

    def test_step2_does_not_update_application_submitted_during_request(self):
        application = self.make_application(
            email="step2-submitted@example.test",
            applicant_type=Application.Type.PARENT,
            status=Application.Status.EMAIL_VERIFIED,
            current_step=2,
            email_verified_at=timezone.now(),
        )
        self.verify_application_browser(application)

        def submit_latest_application():
            Application.objects.filter(pk=application.pk).update(
                status=Application.Status.SUBMITTED
            )

        with self.patch_validation_after_intervening_write(
            ApplicantTypeForm, submit_latest_application
        ):
            response = self.tenant_post(
                reverse("apply_step2", kwargs={"app_id": application.application_id}),
                {
                    "applicant_type": Application.Type.STUDENT,
                    "email": "changed@example.test",
                },
            )

        self.assertRedirects(
            response,
            reverse("apply_submitted", kwargs={"app_id": application.application_id}),
            fetch_redirect_response=False,
        )
        application.refresh_from_db()
        self.assertEqual(application.status, Application.Status.SUBMITTED)
        self.assertEqual(application.applicant_type, Application.Type.PARENT)
        self.assertEqual(application.email, "step2-submitted@example.test")

    def test_program_selection_preserves_newer_answers_and_progress(self):
        application = self.make_application(
            email="program-merge@example.test",
            program=self.alpha_program,
            status=Application.Status.EMAIL_VERIFIED,
            current_step=4,
            data={"existing": "before"},
            email_verified_at=timezone.now(),
        )
        self.verify_application_browser(application)

        def update_latest_application():
            latest = Application.objects.get(pk=application.pk)
            latest.data["step6-custom"] = [
                {
                    "question_id": str(uuid4()),
                    "question_text": "Latest question?",
                    "required": False,
                    "answer": "Latest answer",
                }
            ]
            latest.data["_custom_question_program_id"] = self.alpha_program.pk
            latest.current_step = 8
            latest.save(update_fields=["data", "current_step", "updated_at"])

        with self.patch_validation_after_intervening_write(
            ProgramSelectForm, update_latest_application
        ):
            response = self.tenant_post(
                reverse("apply_step4", kwargs={"app_id": application.application_id}),
                {"program": self.alpha_program.pk},
            )

        self.assertEqual(response.status_code, 302)
        application.refresh_from_db()
        self.assertEqual(application.program_id, self.alpha_program.pk)
        self.assertEqual(application.current_step, 8)
        self.assertEqual(application.data["existing"], "before")
        self.assertEqual(application.data["step6-custom"][0]["answer"], "Latest answer")

    def test_program_selection_round_trips_program_bound_custom_answers(self):
        next_program = Program.objects.create(
            organization=self.alpha,
            name="Alpha Next Program",
            start_date=self.alpha_program.start_date,
            end_date=self.alpha_program.end_date,
            applications_open=self.alpha_program.applications_open,
            applications_close=self.alpha_program.applications_close,
            active=True,
        )
        first_snapshot = [
            {
                "question_id": str(uuid4()),
                "question_text": "What would you like to learn?",
                "required": True,
                "answer": "Build a robot",
            }
        ]
        next_snapshot = [
            {
                "question_id": str(uuid4()),
                "question_text": "What interests you about this program?",
                "required": False,
                "answer": "Working with a team",
            }
        ]
        application = self.make_application(
            program=self.alpha_program,
            status=Application.Status.EMAIL_VERIFIED,
            current_step=6,
            data={
                "step6-custom": first_snapshot,
                "_custom_question_program_id": self.alpha_program.pk,
                "_custom_question_snapshots": {
                    str(next_program.pk): next_snapshot,
                },
            },
            email_verified_at=timezone.now(),
        )
        self.verify_application_browser(application)
        url = reverse("apply_step4", kwargs={"app_id": application.application_id})

        response = self.tenant_post(url, {"program": next_program.pk})

        self.assertEqual(response.status_code, 302)
        application.refresh_from_db()
        self.assertEqual(application.program_id, next_program.pk)
        self.assertEqual(application.data["step6-custom"], next_snapshot)
        self.assertEqual(
            application.data["_custom_question_program_id"], next_program.pk
        )
        self.assertEqual(
            application.data["_custom_question_snapshots"],
            {str(self.alpha_program.pk): first_snapshot},
        )

        response = self.tenant_post(url, {"program": self.alpha_program.pk})

        self.assertEqual(response.status_code, 302)
        application.refresh_from_db()
        self.assertEqual(application.program_id, self.alpha_program.pk)
        self.assertEqual(application.data["step6-custom"], first_snapshot)
        self.assertEqual(
            application.data["_custom_question_program_id"], self.alpha_program.pk
        )
        self.assertEqual(
            application.data["_custom_question_snapshots"],
            {str(next_program.pk): next_snapshot},
        )

    def test_program_selection_rejects_malformed_snapshot_without_changing_program(
        self,
    ):
        next_program = Program.objects.create(
            organization=self.alpha,
            name="Alpha Malformed Snapshot Program",
            start_date=self.alpha_program.start_date,
            end_date=self.alpha_program.end_date,
            applications_open=self.alpha_program.applications_open,
            applications_close=self.alpha_program.applications_close,
            active=True,
        )
        malformed_data = {
            "step6-custom": [],
            "_custom_question_program_id": True,
            "keep": "untouched",
        }
        application = self.make_application(
            program=self.alpha_program,
            status=Application.Status.EMAIL_VERIFIED,
            current_step=6,
            data=malformed_data,
            email_verified_at=timezone.now(),
        )
        self.verify_application_browser(application)

        response = self.tenant_post(
            reverse("apply_step4", kwargs={"app_id": application.application_id}),
            {"program": next_program.pk},
        )

        self.assertEqual(response.status_code, 200)
        application.refresh_from_db()
        self.assertEqual(application.program_id, self.alpha_program.pk)
        self.assertEqual(application.data, malformed_data)
        self.assertContains(response, "custom")

    def test_program_selection_rejects_stale_original_program(self):
        next_program = Program.objects.create(
            organization=self.alpha,
            name="Alpha Concurrent Program",
            start_date=self.alpha_program.start_date,
            end_date=self.alpha_program.end_date,
            applications_open=self.alpha_program.applications_open,
            applications_close=self.alpha_program.applications_close,
            active=True,
        )
        application = self.make_application(
            program=self.alpha_program,
            status=Application.Status.EMAIL_VERIFIED,
            current_step=4,
            data={"keep": "latest"},
            email_verified_at=timezone.now(),
        )
        self.verify_application_browser(application)

        def change_program_during_validation():
            Application.objects.filter(pk=application.pk).update(program=next_program)

        with self.patch_validation_after_intervening_write(
            ProgramSelectForm, change_program_during_validation
        ):
            response = self.tenant_post(
                reverse("apply_step4", kwargs={"app_id": application.application_id}),
                {"program": self.alpha_program.pk},
            )

        self.assertEqual(response.status_code, 200)
        application.refresh_from_db()
        self.assertEqual(application.program_id, next_program.pk)
        self.assertEqual(application.data, {"keep": "latest"})

    def test_program_selection_does_not_update_application_submitted_during_request(
        self,
    ):
        application = self.make_application(
            email="program-submitted@example.test",
            status=Application.Status.EMAIL_VERIFIED,
            current_step=4,
            email_verified_at=timezone.now(),
        )
        self.verify_application_browser(application)

        def submit_latest_application():
            Application.objects.filter(pk=application.pk).update(
                status=Application.Status.SUBMITTED
            )

        with self.patch_validation_after_intervening_write(
            ProgramSelectForm, submit_latest_application
        ):
            response = self.tenant_post(
                reverse("apply_step4", kwargs={"app_id": application.application_id}),
                {"program": self.alpha_program.pk},
            )

        self.assertRedirects(
            response,
            reverse("apply_submitted", kwargs={"app_id": application.application_id}),
            fetch_redirect_response=False,
        )
        application.refresh_from_db()
        self.assertEqual(application.status, Application.Status.SUBMITTED)
        self.assertIsNone(application.program_id)

    def test_parent_handoff_merges_current_snapshot_and_progress(self):
        application = self.make_application(
            email="student-handoff@example.test",
            applicant_type=Application.Type.STUDENT,
            status=Application.Status.EMAIL_VERIFIED,
            current_step=6,
            data={"step5-student": {"personal_email": "student-personal@example.test"}},
            email_verified_at=timezone.now(),
        )
        self.verify_application_browser(application)
        from applications.views.steps_student import Step7PrimaryParentView

        original_build_forms = Step7PrimaryParentView._build_forms
        changed = False

        def build_forms(view, current, post=None):
            nonlocal changed
            result = original_build_forms(view, current, post)
            if post is not None and not changed:
                changed = True
                latest = Application.objects.get(pk=application.pk)
                latest.data["step6-custom"] = [
                    {"question_text": "New question?", "answer": "New answer"}
                ]
                latest.current_step = 9
                latest.save(update_fields=["data", "current_step", "updated_at"])
            return result

        with patch.object(Step7PrimaryParentView, "_build_forms", new=build_forms):
            response = self.tenant_post(
                reverse("apply_step7", kwargs={"app_id": application.application_id}),
                {"parent_email": "parent-handoff@example.test"},
            )

        self.assertEqual(response.status_code, 302)
        application.refresh_from_db()
        self.assertEqual(application.status, Application.Status.AWAITING_PARENT)
        self.assertEqual(application.current_step, 9)
        self.assertEqual(application.data["step6-custom"][0]["answer"], "New answer")
        self.assertEqual(
            application.data["step7_handoff"]["parent_email"],
            "parent-handoff@example.test",
        )
        self.assertTrue(application.handoff_token)

    def test_parent_handoff_revalidates_current_student_email(self):
        application = self.make_application(
            email="student-handoff-current@example.test",
            applicant_type=Application.Type.STUDENT,
            status=Application.Status.EMAIL_VERIFIED,
            current_step=6,
            data={"step5-student": {"personal_email": "old-student@example.test"}},
            email_verified_at=timezone.now(),
        )
        self.verify_application_browser(application)
        from applications.views.steps_student import Step7PrimaryParentView

        original_build_forms = Step7PrimaryParentView._build_forms
        changed = False

        def build_forms(view, current, post=None):
            nonlocal changed
            result = original_build_forms(view, current, post)
            if post is not None and not changed:
                changed = True
                latest = Application.objects.get(pk=application.pk)
                latest.data["step5-student"][
                    "personal_email"
                ] = "parent-handoff@example.test"
                latest.save(update_fields=["data", "updated_at"])
            return result

        mail.outbox = []
        with patch.object(Step7PrimaryParentView, "_build_forms", new=build_forms):
            response = self.tenant_post(
                reverse("apply_step7", kwargs={"app_id": application.application_id}),
                {"parent_email": "parent-handoff@example.test"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, "You cannot use your own email address for your parent/guardian."
        )
        application.refresh_from_db()
        self.assertEqual(application.status, Application.Status.EMAIL_VERIFIED)
        self.assertNotIn("step7_handoff", application.data)
        self.assertFalse(application.handoff_token)
        self.assertEqual(mail.outbox, [])

    def test_parent_handoff_rejects_application_submitted_during_request(self):
        application = self.make_application(
            email="student-handoff-submitted@example.test",
            applicant_type=Application.Type.STUDENT,
            status=Application.Status.EMAIL_VERIFIED,
            current_step=6,
            email_verified_at=timezone.now(),
        )
        self.verify_application_browser(application)
        from applications.views.steps_student import Step7PrimaryParentView

        original_build_forms = Step7PrimaryParentView._build_forms
        changed = False

        def build_forms(view, current, post=None):
            nonlocal changed
            result = original_build_forms(view, current, post)
            if post is not None and not changed:
                changed = True
                Application.objects.filter(pk=application.pk).update(
                    status=Application.Status.SUBMITTED
                )
            return result

        mail.outbox = []
        with patch.object(Step7PrimaryParentView, "_build_forms", new=build_forms):
            response = self.tenant_post(
                reverse("apply_step7", kwargs={"app_id": application.application_id}),
                {"parent_email": "parent-handoff@example.test"},
            )

        self.assertRedirects(
            response,
            reverse("apply_submitted", kwargs={"app_id": application.application_id}),
            fetch_redirect_response=False,
        )
        application.refresh_from_db()
        self.assertEqual(application.status, Application.Status.SUBMITTED)
        self.assertNotIn("step7_handoff", application.data)
        self.assertFalse(application.handoff_token)
        self.assertEqual(mail.outbox, [])

    def test_parent_swap_uses_current_parent_data_and_preserves_other_answers(self):
        application = self.make_application(
            email="parent-swap@example.test",
            applicant_type=Application.Type.PARENT,
            status=Application.Status.EMAIL_VERIFIED,
            current_step=8,
            data={
                "step7-primaryparent": {"legal_first_name": "Old primary"},
                "step8-secondaryparent": {"legal_first_name": "Old secondary"},
            },
            email_verified_at=timezone.now(),
        )
        self.verify_application_browser(application)
        from applications.views.steps_student import _is_handoff_authorized

        changed = False

        def authorize(request, current):
            nonlocal changed
            authorized = _is_handoff_authorized(request, current)
            if not changed:
                changed = True
                latest = Application.objects.get(pk=application.pk)
                latest.data["step7-primaryparent"] = {
                    "legal_first_name": "Current primary"
                }
                latest.data["step8-secondaryparent"] = {
                    "legal_first_name": "Current secondary"
                }
                latest.data["step6-custom"] = [
                    {"question_text": "New question?", "answer": "Kept"}
                ]
                latest.save(update_fields=["data", "updated_at"])
            return authorized

        with patch(
            "applications.views.steps_student._is_handoff_authorized", authorize
        ):
            response = self.tenant_post(
                reverse(
                    "apply_swap_parents", kwargs={"app_id": application.application_id}
                )
            )

        self.assertEqual(response.status_code, 302)
        application.refresh_from_db()
        self.assertEqual(
            application.data["step7-primaryparent"]["legal_first_name"],
            "Current secondary",
        )
        self.assertEqual(
            application.data["step8-secondaryparent"]["legal_first_name"],
            "Current primary",
        )
        self.assertEqual(application.data["step6-custom"][0]["answer"], "Kept")

    def test_parent_swap_rejects_application_submitted_during_request(self):
        application = self.make_application(
            email="parent-swap-submitted@example.test",
            applicant_type=Application.Type.PARENT,
            status=Application.Status.EMAIL_VERIFIED,
            current_step=8,
            data={
                "step7-primaryparent": {"legal_first_name": "Primary"},
                "step8-secondaryparent": {"legal_first_name": "Secondary"},
            },
            email_verified_at=timezone.now(),
        )
        self.verify_application_browser(application)
        from applications.views.steps_student import _is_handoff_authorized

        changed = False

        def authorize(request, current):
            nonlocal changed
            authorized = _is_handoff_authorized(request, current)
            if not changed:
                changed = True
                Application.objects.filter(pk=application.pk).update(
                    status=Application.Status.SUBMITTED
                )
            return authorized

        with patch(
            "applications.views.steps_student._is_handoff_authorized", authorize
        ):
            response = self.tenant_post(
                reverse(
                    "apply_swap_parents", kwargs={"app_id": application.application_id}
                )
            )

        self.assertRedirects(
            response,
            reverse("apply_submitted", kwargs={"app_id": application.application_id}),
            fetch_redirect_response=False,
        )
        application.refresh_from_db()
        self.assertEqual(application.status, Application.Status.SUBMITTED)
        self.assertEqual(
            application.data["step7-primaryparent"]["legal_first_name"], "Primary"
        )
        self.assertEqual(
            application.data["step8-secondaryparent"]["legal_first_name"], "Secondary"
        )

    def test_parent_handoff_and_swap_reject_foreign_organization_application(self):
        application = self.make_application(
            organization=self.beta,
            email="foreign-student@example.test",
            applicant_type=Application.Type.STUDENT,
            status=Application.Status.EMAIL_VERIFIED,
            data={"original": "unchanged"},
            email_verified_at=timezone.now(),
        )
        from applications.views.steps_student import (
            Step7PrimaryParentView,
            SwapParentsView,
        )

        for view_class, route, data in (
            (
                Step7PrimaryParentView,
                "apply_step7",
                {"parent_email": "foreign-parent@example.test"},
            ),
            (SwapParentsView, "apply_swap_parents", {}),
        ):
            with self.subTest(route=route):
                request = RequestFactory().post(
                    reverse(route, kwargs={"app_id": application.application_id}),
                    data,
                )
                request.organization = self.alpha
                request.session = {}
                with self.assertRaises(Http404):
                    view_class.as_view()(request, app_id=application.application_id)
        application.refresh_from_db()
        self.assertEqual(application.data, {"original": "unchanged"})
        self.assertEqual(application.status, Application.Status.EMAIL_VERIFIED)

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
        self.verify_application_browser(application)

        response = self.tenant_get(
            reverse("apply_step5", kwargs={"app_id": application.application_id})
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "BetaPrivateStudent")
        self.assertNotContains(response, "88 Beta Confidential Road")

    def test_foreign_email_match_does_not_make_local_student_identity_ambiguous(self):
        Student.objects.create(
            organization=self.alpha,
            legal_first_name="AlphaReturningStudent",
            last_name="Applicant",
            personal_email="shared-tenant-email@example.test",
            address="Alpha Student Address",
        )
        Adult.objects.create(
            organization=self.beta,
            legal_first_name="BetaPrivateAdult",
            last_name="Applicant",
            personal_email="shared-tenant-email@example.test",
            address="Beta Private Adult Address",
        )
        application = self.make_application(
            email="shared-tenant-email@example.test",
            applicant_type=Application.Type.STUDENT,
            status=Application.Status.EMAIL_VERIFIED,
            current_step=5,
            email_verified_at=timezone.now(),
        )
        self.verify_application_browser(application)

        response = self.tenant_get(
            reverse("apply_step5", kwargs={"app_id": application.application_id})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "AlphaReturningStudent")
        self.assertContains(response, "Alpha Student Address")
        self.assertNotContains(response, "BetaPrivateAdult")
        self.assertNotContains(response, "Beta Private Adult Address")

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
        self.verify_application_browser(application)

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
        self.verify_application_browser(application)

        response = self.tenant_get(
            reverse("apply_step7", kwargs={"app_id": application.application_id})
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "LinkedBetaGuardian")
        self.assertNotContains(response, "77 Linked Beta Road")

    def test_unrelated_verified_applicant_cannot_prefill_or_swap_linked_family_data(
        self,
    ):
        student, primary, secondary = self.make_linked_family(
            "existing-student@example.test",
            "existing-primary@example.test",
            "existing-secondary@example.test",
        )
        application = self.make_application(
            email="unrelated-applicant@example.test",
            applicant_type=Application.Type.PARENT,
            status=Application.Status.EMAIL_VERIFIED,
            current_step=8,
            data={"step5-student": {"_existing_student_id": student.pk}},
            email_verified_at=timezone.now(),
        )
        self.verify_application_browser(application)

        primary_response = self.tenant_get(
            reverse("apply_step7", kwargs={"app_id": application.application_id})
        )
        secondary_response = self.tenant_get(
            reverse("apply_step8", kwargs={"app_id": application.application_id})
        )

        self.assertEqual(primary_response.status_code, 200)
        self.assertEqual(secondary_response.status_code, 200)
        self.assertNotContains(primary_response, primary.legal_first_name)
        self.assertNotContains(primary_response, primary.address)
        self.assertNotContains(secondary_response, secondary.legal_first_name)
        self.assertNotContains(secondary_response, student.address)

        self.tenant_post(
            reverse("apply_swap_parents", kwargs={"app_id": application.application_id})
        )
        application.refresh_from_db()
        self.assertNotIn("step7-primaryparent", application.data)
        self.assertNotIn("step8-secondaryparent", application.data)

    def test_ambiguous_verified_guardian_cannot_prefill_linked_family_data(self):
        student, primary, secondary = self.make_linked_family(
            "existing-student@example.test",
            "shared-guardian@example.test",
            "existing-secondary@example.test",
        )
        Adult.objects.create(
            organization=self.alpha,
            legal_first_name="Other Shared Guardian",
            last_name="Applicant",
            personal_email=primary.personal_email,
            is_parent=True,
        )
        application = self.make_application(
            email=primary.personal_email,
            applicant_type=Application.Type.PARENT,
            status=Application.Status.EMAIL_VERIFIED,
            current_step=8,
            data={"step5-student": {"_existing_student_id": student.pk}},
            email_verified_at=timezone.now(),
        )
        self.verify_application_browser(application)

        primary_response = self.tenant_get(
            reverse("apply_step7", kwargs={"app_id": application.application_id})
        )
        secondary_response = self.tenant_get(
            reverse("apply_step8", kwargs={"app_id": application.application_id})
        )

        self.assertEqual(primary_response.status_code, 200)
        self.assertEqual(secondary_response.status_code, 200)
        self.assertNotContains(primary_response, primary.legal_first_name)
        self.assertNotContains(secondary_response, secondary.legal_first_name)

    def test_handoff_recipient_must_be_related_guardian_for_student_prefill(self):
        student, primary, secondary = self.make_linked_family(
            "verified-student@example.test",
            "existing-primary@example.test",
            "existing-secondary@example.test",
        )
        handoff_email = "unrelated-handoff@example.test"
        application = self.make_application(
            email=student.personal_email,
            applicant_type=Application.Type.STUDENT,
            status=Application.Status.AWAITING_PARENT,
            current_step=7,
            data={
                "step5-student": {"_existing_student_id": student.pk},
                "step7_handoff": {"parent_email": handoff_email},
            },
            email_verified_at=timezone.now(),
        )
        application.issue_handoff_token()
        application.save(update_fields=["handoff_token"])
        self.verify_application_browser(application, expected_recipient=handoff_email)

        primary_response = self.tenant_get(
            reverse("apply_step7", kwargs={"app_id": application.application_id})
        )
        secondary_response = self.tenant_get(
            reverse("apply_step8", kwargs={"app_id": application.application_id})
        )

        self.assertEqual(primary_response.status_code, 200)
        self.assertEqual(secondary_response.status_code, 200)
        self.assertNotContains(primary_response, primary.legal_first_name)
        self.assertNotContains(secondary_response, secondary.legal_first_name)
        self.assertNotContains(secondary_response, student.address)

    def test_unique_related_guardian_can_prefill_existing_family(self):
        student, primary, secondary = self.make_linked_family(
            "existing-student@example.test",
            "verified-primary@example.test",
            "existing-secondary@example.test",
        )
        application = self.make_application(
            email=primary.personal_email,
            applicant_type=Application.Type.PARENT,
            status=Application.Status.EMAIL_VERIFIED,
            current_step=8,
            data={"step5-student": {"_existing_student_id": student.pk}},
            email_verified_at=timezone.now(),
        )
        self.verify_application_browser(application)

        primary_response = self.tenant_get(
            reverse("apply_step7", kwargs={"app_id": application.application_id})
        )
        secondary_response = self.tenant_get(
            reverse("apply_step8", kwargs={"app_id": application.application_id})
        )

        self.assertContains(primary_response, primary.legal_first_name)
        self.assertContains(secondary_response, secondary.legal_first_name)
        self.assertContains(secondary_response, student.address)

    def test_malformed_and_foreign_student_references_do_not_prefill(self):
        malformed_application = self.make_application(
            email="unrelated-malformed@example.test",
            applicant_type=Application.Type.PARENT,
            status=Application.Status.EMAIL_VERIFIED,
            current_step=8,
            data={"step5-student": {"_existing_student_id": ["not", "an", "id"]}},
            email_verified_at=timezone.now(),
        )
        self.verify_application_browser(malformed_application)
        malformed_response = self.tenant_get(
            reverse(
                "apply_step7",
                kwargs={"app_id": malformed_application.application_id},
            )
        )
        self.assertEqual(malformed_response.status_code, 200)

        foreign_student = Student.objects.create(
            organization=self.beta,
            legal_first_name="Private Foreign Student",
            last_name="Applicant",
            address="Private Foreign Address",
        )
        foreign_guardian = Adult.objects.create(
            organization=self.beta,
            legal_first_name="Private Foreign Guardian",
            last_name="Applicant",
            personal_email="foreign-guardian@example.test",
            is_parent=True,
        )
        foreign_relationship = AdultStudentRelationship.objects.create(
            organization=self.beta,
            adult=foreign_guardian,
            student=foreign_student,
        )
        foreign_student.primary_contact_relationship = foreign_relationship
        foreign_student.save(update_fields=["primary_contact_relationship"])
        foreign_application = self.make_application(
            email="unrelated-foreign@example.test",
            applicant_type=Application.Type.PARENT,
            status=Application.Status.EMAIL_VERIFIED,
            current_step=8,
            data={"step5-student": {"_existing_student_id": foreign_student.pk}},
            email_verified_at=timezone.now(),
        )
        self.verify_application_browser(foreign_application)
        foreign_response = self.tenant_get(
            reverse(
                "apply_step7", kwargs={"app_id": foreign_application.application_id}
            )
        )
        self.assertEqual(foreign_response.status_code, 200)
        self.assertNotContains(foreign_response, foreign_guardian.legal_first_name)
        self.assertNotContains(foreign_response, foreign_student.address)

    def test_mismatched_contact_relationship_is_not_used_for_prefill(self):
        student, _, _ = self.make_linked_family(
            "verified-student@example.test",
            "existing-primary@example.test",
            "existing-secondary@example.test",
        )
        other_student = Student.objects.create(
            organization=self.alpha,
            legal_first_name="Other Student",
            last_name="Applicant",
        )
        other_guardian = Adult.objects.create(
            organization=self.alpha,
            legal_first_name="Wrong Relationship Guardian",
            last_name="Applicant",
            personal_email="other-guardian@example.test",
            is_parent=True,
        )
        mismatched_relationship = AdultStudentRelationship.objects.create(
            organization=self.alpha,
            adult=other_guardian,
            student=other_student,
        )
        student.primary_contact_relationship = mismatched_relationship
        student.save(update_fields=["primary_contact_relationship"])
        application = self.make_application(
            email=student.personal_email,
            applicant_type=Application.Type.STUDENT,
            status=Application.Status.EMAIL_VERIFIED,
            current_step=7,
            data={"step5-student": {"_existing_student_id": student.pk}},
            email_verified_at=timezone.now(),
        )
        self.verify_application_browser(application)

        response = self.tenant_get(
            reverse("apply_step7", kwargs={"app_id": application.application_id})
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, other_guardian.legal_first_name)

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
