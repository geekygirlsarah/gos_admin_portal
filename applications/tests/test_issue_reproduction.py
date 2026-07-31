from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from applications.models import Application
from programs.models import Program

User = get_user_model()


class ApplicationReviewGroupingTests(TestCase):
    def setUp(self):
        self.group, _ = Group.objects.get_or_create(name="LeadMentor")
        from django.contrib.contenttypes.models import ContentType

        ct = ContentType.objects.get_for_model(Application)
        perm, _ = Permission.objects.get_or_create(
            codename="review_application",
            content_type=ct,
        )
        self.group.permissions.add(perm)

        self.user = User.objects.create_user(
            username="lead", password="password"
        )  # nosec B106
        self.user.groups.add(self.group)
        self.client.force_login(self.user)

        self.program = Program.objects.create(name="Test Program", active=True)

        # Create applications with various statuses
        self.draft = Application.objects.create(
            status=Application.Status.DRAFT,
            program=self.program,
            email="draft@example.com",
        )
        self.verified = Application.objects.create(
            status=Application.Status.EMAIL_VERIFIED,
            program=self.program,
            email="verified@example.com",
        )
        self.awaiting_parent = Application.objects.create(
            status=Application.Status.AWAITING_PARENT,
            program=self.program,
            email="parent@example.com",
        )
        self.submitted = Application.objects.create(
            status=Application.Status.SUBMITTED,
            program=self.program,
            email="submitted@example.com",
            submitted_at=timezone.now(),
        )
        self.approved = Application.objects.create(
            status=Application.Status.APPROVED,
            program=self.program,
            email="approved@example.com",
            submitted_at=timezone.now(),
        )
        self.approved_signed = Application.objects.create(
            status=Application.Status.APPROVED_SIGNED,
            program=self.program,
            email="signed@example.com",
            submitted_at=timezone.now(),
        )
        self.converted = Application.objects.create(
            status=Application.Status.CONVERTED,
            program=self.program,
            email="converted@example.com",
            submitted_at=timezone.now(),
        )

    def test_grouping_sections_present(self):
        """Verify that the requested grouping sections are present in the response."""
        response = self.client.get(reverse("application_review_list"))
        self.assertEqual(response.status_code, 200)

        # Admin actions
        self.assertContains(response, "Review to convert to student")
        self.assertContains(response, "Review to approve application")

        # Applicant actions
        self.assertContains(response, "Waiting on forms to be signed")
        self.assertContains(response, "Waiting on Parent data")
        self.assertContains(response, "Waiting on Student data")
        self.assertContains(response, "No data yet")

    def test_program_filter_present(self):
        """Verify that the 'Applicant Program' filter is present and 'Applicant Type' is replaced."""
        response = self.client.get(reverse("application_review_list"))
        self.assertEqual(response.status_code, 200)

        self.assertContains(response, "Applicant Program")
        self.assertNotContains(
            response, "Applicant type"
        )  # Casing might matter, but usually it's "Applicant type" in templates

    def test_filter_by_program(self):
        """Verify filtering by program works."""
        other_program = Program.objects.create(name="Other Program", active=True)
        other_app = Application.objects.create(
            status=Application.Status.SUBMITTED,
            program=other_program,
            email="other@example.com",
            submitted_at=timezone.now(),
        )

        url = reverse("application_review_list") + f"?program={other_program.id}"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        self.assertContains(response, other_app.application_id)
        self.assertNotContains(response, self.submitted.application_id)
