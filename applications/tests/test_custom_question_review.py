from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from applications.models import Application
from organizations.models import Organization, UserOrganizationMembership
from programs.models import Program


class CustomQuestionReviewTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            name="Review", slug="question-review", subdomain="question-review"
        )
        self.other = Organization.objects.create(
            name="Other",
            slug="question-review-other",
            subdomain="question-review-other",
        )
        self.program = Program.objects.create(organization=self.org, name="Program")
        self.app = Application.objects.create(
            organization=self.org,
            program=self.program,
            applicant_type=Application.Type.PARENT,
            status=Application.Status.SUBMITTED,
            data={
                "step6-custom": [
                    {
                        "question_id": str(uuid4()),
                        "question_text": "What would you build?",
                        "answer": "A robot",
                    },
                    {
                        "question_id": str(uuid4()),
                        "question_text": "Anything else?",
                        "answer": "",
                    },
                ],
                "_custom_question_program_id": self.program.pk,
            },
        )
        self.lead = get_user_model().objects.create_user(
            username="question-review-lead"
        )
        self.membership = UserOrganizationMembership.objects.create(
            organization=self.org, user=self.lead, role="LeadMentor", is_active=True
        )
        self.client.force_login(self.lead)
        self.url = reverse("application_review_detail", args=[self.app.application_id])

    def test_lead_sees_questions_and_answers_not_raw_snapshot(self):
        response = self.client.get(self.url, HTTP_HOST="question-review.localhost")
        self.assertContains(response, "What would you build?")
        self.assertContains(response, "A robot")
        self.assertContains(response, "Anything else?")
        self.assertNotContains(response, "question_id")
        self.assertNotContains(response, "_custom_question_program_id")

    def test_malformed_snapshot_is_reported_without_discarding_it(self):
        for value in (None, [{"question_text": "Bad", "answer": []}]):
            with self.subTest(value=value):
                self.app.data["step6-custom"] = value
                self.app.save(update_fields=["data"])
                response = self.client.get(
                    self.url, HTTP_HOST="question-review.localhost"
                )
                self.assertContains(
                    response, "Custom application questions are malformed"
                )
                self.app.refresh_from_db()
                self.assertEqual(self.app.data["step6-custom"], value)

    def test_non_lead_and_foreign_lead_cannot_see_answers(self):
        self.membership.role = "Mentor"
        self.membership.save()
        response = self.client.get(self.url, HTTP_HOST="question-review.localhost")
        self.assertNotEqual(response.status_code, 200)
        self.assertNotIn(b"A robot", response.content)
        UserOrganizationMembership.objects.create(
            organization=self.other, user=self.lead, role="LeadMentor", is_active=True
        )
        response = self.client.get(
            self.url, HTTP_HOST="question-review-other.localhost"
        )
        self.assertNotEqual(response.status_code, 200)
        self.assertNotIn(b"A robot", response.content)
