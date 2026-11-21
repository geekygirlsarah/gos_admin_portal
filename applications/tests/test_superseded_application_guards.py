import secrets
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from applications.forms import ResumeApplicationForm
from applications.models import Application
from applications.reminders import run_application_reminder_batch
from applications.services import applications_for_user
from applications.views.utils import _save_step_data
from organizations.models import Organization
from programs.models import Adult


@override_settings(ALLOWED_HOSTS=[".localhost", "testserver"])
class SupersededApplicationGuardTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(
            name="Superseded Alpha",
            slug="superseded-alpha",
            subdomain="superseded-alpha",
            is_active=True,
        )
        self.other_organization = Organization.objects.create(
            name="Superseded Beta",
            slug="superseded-beta",
            subdomain="superseded-beta",
            is_active=True,
        )
        self.host = f"{self.organization.subdomain}.localhost"

    def make_application(self, **kwargs):
        defaults = {
            "organization": self.organization,
            "applicant_type": Application.Type.PARENT,
            "email": "applicant@example.test",
            "email_verified_at": timezone.now(),
            "status": Application.Status.DRAFT,
            "current_step": 4,
        }
        defaults.update(kwargs)
        return Application.objects.create(**defaults)

    def test_superseded_application_is_not_available_on_applicant_routes(self):
        application = self.make_application(
            superseded_at=timezone.now(),
            status=Application.Status.SUBMITTED,
        )

        for route_name in (
            "apply_step2",
            "apply_browser_verification",
            "apply_step10_document_access",
        ):
            with self.subTest(route_name=route_name):
                response = self.client.get(
                    reverse(route_name, kwargs={"app_id": application.application_id}),
                    HTTP_HOST=self.host,
                )
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response["Location"], reverse("apply_start"))

    def test_superseded_application_cannot_be_resumed_by_identifier(self):
        application = self.make_application(superseded_at=timezone.now())

        form = ResumeApplicationForm(
            {"application_id": application.application_id},
            organization=self.organization,
        )

        self.assertFalse(form.is_valid())

    def test_superseded_application_is_hidden_from_reviewer_routes(self):
        application = self.make_application(
            superseded_at=timezone.now(),
            status=Application.Status.SUBMITTED,
        )
        reviewer = get_user_model().objects.create_superuser(
            username="superseded-reviewer",
            email="reviewer@example.test",
            password=secrets.token_urlsafe(32),
        )
        self.client.force_login(reviewer)

        list_response = self.client.get(
            reverse("application_review_list"), HTTP_HOST=self.host
        )
        detail_response = self.client.get(
            reverse(
                "application_review_detail",
                kwargs={"app_id": application.application_id},
            ),
            HTTP_HOST=self.host,
        )

        self.assertNotContains(list_response, application.application_id)
        self.assertEqual(detail_response.status_code, 302)
        self.assertEqual(detail_response["Location"], reverse("apply_start"))

    def test_stale_locked_step_write_cannot_edit_superseded_application(self):
        application = self.make_application()
        original_data = application.data
        Application.objects.filter(pk=application.pk).update(
            superseded_at=timezone.now()
        )

        with self.assertRaises(Application.DoesNotExist):
            _save_step_data(
                application,
                "step5-student",
                {"legal_first_name": "Changed"},
                next_step=6,
            )

        application.refresh_from_db()
        self.assertEqual(application.data, original_data)

    def test_superseded_application_is_not_in_logged_in_user_dashboard(self):
        user = get_user_model().objects.create_user(
            username="superseded-applicant",
            email="applicant@example.test",
            password=secrets.token_urlsafe(32),
        )
        Adult.objects.create(
            organization=self.organization,
            user=user,
            legal_first_name="Applicant",
            last_name="Test",
            personal_email="applicant@example.test",
            is_parent=True,
        )
        application = self.make_application(superseded_at=timezone.now())

        self.assertNotIn(
            application,
            applications_for_user(user, organization=self.organization),
        )

    def test_superseded_application_does_not_receive_reminders(self):
        now = timezone.now()
        application = self.make_application(
            superseded_at=now,
            applicant_progress_at=now - timedelta(days=3),
        )

        result = run_application_reminder_batch(
            self.organization,
            now=now,
            dry_run=True,
        )

        self.assertEqual(result["planned"], 0)
        self.assertEqual(mail.outbox, [])

    def test_superseded_application_in_another_tenant_does_not_hide_local_resume(self):
        local_application = self.make_application()
        other_application = self.make_application(
            organization=self.other_organization,
            superseded_at=timezone.now(),
        )

        local_form = ResumeApplicationForm(
            {"application_id": local_application.application_id},
            organization=self.organization,
        )
        foreign_form = ResumeApplicationForm(
            {"application_id": other_application.application_id},
            organization=self.organization,
        )

        self.assertTrue(local_form.is_valid())
        self.assertFalse(foreign_form.is_valid())
