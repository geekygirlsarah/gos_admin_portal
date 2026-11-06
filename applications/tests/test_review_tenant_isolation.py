from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core import mail
from django.http import Http404
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from applications.models import Application, ApplicationDocumentSubmission
from applications.views.review import ApplicationCleanupView, ApplicationReviewListView
from organizations.models import Organization, UserOrganizationMembership
from programs.models import Program, ProgramDocument


class ApplicationReviewTenantIsolationTests(TestCase):
    def setUp(self):
        self.alpha = Organization.objects.create(
            name="Review Alpha",
            slug="review-alpha",
            subdomain="review-alpha",
        )
        self.beta = Organization.objects.create(
            name="Review Beta",
            slug="review-beta",
            subdomain="review-beta",
        )
        self.alpha_program = Program.objects.create(
            organization=self.alpha,
            name="Alpha Program",
        )
        self.beta_program = Program.objects.create(
            organization=self.beta,
            name="Beta Program",
        )
        self.user = get_user_model().objects.create_user(
            username="review-alpha-lead",
            email="review-alpha-lead@example.test",
        )
        content_type = ContentType.objects.get_for_model(Application)
        permission, _ = Permission.objects.get_or_create(
            content_type=content_type,
            codename="review_application",
            defaults={"name": "Can review applications"},
        )
        group, _ = Group.objects.get_or_create(name="LeadMentor")
        group.permissions.add(permission)
        self.user.groups.add(group)
        UserOrganizationMembership.objects.create(
            user=self.user,
            organization=self.alpha,
            role="LeadMentor",
            is_active=True,
        )
        self.client.force_login(self.user)
        self.alpha_host = "review-alpha.localhost"

    def make_application(
        self,
        organization=None,
        program=None,
        email="applicant@example.test",
        status=Application.Status.SUBMITTED,
    ):
        return Application.objects.create(
            organization=organization or self.alpha,
            program=program or self.alpha_program,
            applicant_type=Application.Type.PARENT,
            email=email,
            current_step=8,
            email_verified_at=timezone.now(),
            status=status,
            submitted_at=timezone.now(),
            data={
                "step5-student": {
                    "legal_first_name": "Test",
                    "last_name": "Applicant",
                },
                "step7-primaryparent": {
                    "legal_first_name": "Test",
                    "last_name": "Parent",
                },
            },
        )

    def tenant_get(self, path, **kwargs):
        return self.client.get(path, HTTP_HOST=self.alpha_host, **kwargs)

    def tenant_post(self, path, data=None, **kwargs):
        return self.client.post(
            path, data=data or {}, HTTP_HOST=self.alpha_host, **kwargs
        )

    def test_foreign_application_review_pages_are_not_visible(self):
        application = self.make_application(
            organization=self.beta,
            program=self.beta_program,
            email="private-beta-applicant@example.test",
        )
        routes = (
            "application_review_detail",
            "application_review_decline",
            "application_review_edit",
            "application_review_delete",
        )

        for route in routes:
            with self.subTest(route=route):
                response = self.tenant_get(
                    reverse(route, kwargs={"app_id": application.application_id})
                )
                self.assertNotEqual(response.status_code, 200)
                self.assertNotIn(
                    b"private-beta-applicant@example.test", response.content
                )

    def test_foreign_application_actions_do_not_change_or_delete_it(self):
        actions = (
            ("application_review_approve", {}),
            ("application_review_decline", {"reason": "Not eligible"}),
            ("application_review_edit", {}),
            ("application_review_delete", {}),
            ("application_review_resend_email", {"type": "otp"}),
        )
        for route, data in actions:
            with self.subTest(route=route):
                application = self.make_application(
                    organization=self.beta,
                    program=self.beta_program,
                    email=f"{route}@private.example.test",
                )
                original_data = application.data
                response = self.tenant_post(
                    reverse(route, kwargs={"app_id": application.application_id}),
                    data,
                )
                self.assertNotEqual(response.status_code, 200)
                self.assertTrue(Application.objects.filter(pk=application.pk).exists())
                application.refresh_from_db()
                self.assertEqual(application.status, Application.Status.SUBMITTED)
                self.assertEqual(application.data, original_data)
                self.assertEqual(mail.outbox, [])

    def test_stale_cleanup_deletes_only_current_organization_applications(self):
        alpha_app = self.make_application(
            email="old-alpha@example.test",
        )
        beta_app = self.make_application(
            organization=self.beta,
            program=self.beta_program,
            email="old-beta@example.test",
        )
        stale_time = timezone.now() - timedelta(days=31)
        Application.objects.filter(pk__in=[alpha_app.pk, beta_app.pk]).update(
            created_at=stale_time
        )

        response = self.tenant_post(reverse("application_cleanup_stale"))

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Application.objects.filter(pk=alpha_app.pk).exists())
        self.assertTrue(Application.objects.filter(pk=beta_app.pk).exists())

    def test_stale_cleanup_preserves_application_with_foreign_submission(self):
        application = self.make_application(
            email="old-alpha-with-foreign-child@example.test"
        )
        document = ProgramDocument.objects.create(
            program=self.alpha_program,
            name="Alpha release",
            file="blank-alpha-release.pdf",
        )
        submission = ApplicationDocumentSubmission.objects.create(
            application=application,
            organization=self.beta,
            document=document,
            file="application_documents/private-beta-signed.pdf",
        )
        Application.objects.filter(pk=application.pk).update(
            created_at=timezone.now() - timedelta(days=31)
        )

        self.tenant_post(reverse("application_cleanup_stale"))

        self.assertTrue(Application.objects.filter(pk=application.pk).exists())
        self.assertTrue(
            ApplicationDocumentSubmission.objects.filter(pk=submission.pk).exists()
        )

    def test_review_list_program_choices_are_tenant_scoped(self):
        response = self.tenant_get(reverse("application_review_list"))

        self.assertEqual(response.status_code, 200)
        programs = list(response.context["programs"])
        self.assertEqual(programs, [self.alpha_program])

    def test_application_with_foreign_program_is_not_reviewed(self):
        application = self.make_application(
            organization=self.alpha,
            program=self.beta_program,
            email="mismatched-program@example.test",
        )

        response = self.tenant_get(
            reverse(
                "application_review_detail",
                kwargs={"app_id": application.application_id},
            )
        )

        self.assertNotEqual(response.status_code, 200)
        self.assertNotIn(b"mismatched-program@example.test", response.content)

    def test_foreign_owned_document_submission_is_not_exposed_or_deleted(self):
        application = self.make_application()
        document = ProgramDocument.objects.create(
            program=self.alpha_program,
            name="Alpha release",
            file="blank-alpha-release.pdf",
            is_required=True,
            is_active=True,
        )
        submission = ApplicationDocumentSubmission.objects.create(
            application=application,
            organization=self.beta,
            document=document,
            file="application_documents/private-beta-signed.pdf",
        )

        response = self.tenant_get(
            reverse(
                "application_review_detail",
                kwargs={"app_id": application.application_id},
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["documents_status"][0]["submission"])

        self.tenant_post(
            reverse(
                "application_review_delete",
                kwargs={"app_id": application.application_id},
            )
        )
        self.assertTrue(Application.objects.filter(pk=application.pk).exists())
        self.assertTrue(
            ApplicationDocumentSubmission.objects.filter(pk=submission.pk).exists()
        )

    def test_missing_organization_context_fails_closed(self):
        request = RequestFactory().get(reverse("application_review_list"))
        request.user = self.user
        request.organization = None

        with self.assertRaises(Http404):
            ApplicationReviewListView.as_view()(request)

        cleanup_request = RequestFactory().post(reverse("application_cleanup_stale"))
        cleanup_request.user = self.user
        cleanup_request.organization = None

        with self.assertRaises(Http404):
            ApplicationCleanupView.as_view()(cleanup_request)
