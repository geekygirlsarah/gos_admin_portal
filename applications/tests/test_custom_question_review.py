from unittest.mock import patch
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

    def test_lead_can_edit_answers_without_changing_snapshot_metadata(self):
        edit = reverse("application_review_edit", args=[self.app.application_id])
        response = self.client.get(edit, HTTP_HOST="question-review.localhost")
        self.assertContains(response, "What would you build?")
        token = response.context["form"].initial["custom_snapshot_version"]
        original = self.app.data["step6-custom"]
        with patch("applications.views.review.log_event") as log_event:
            response = self.client.post(
                edit,
                {
                    "custom_snapshot_version": token,
                    f"custom_answer_{original[0]['question_id']}": "A bridge",
                    f"custom_answer_{original[1]['question_id']}": "More ideas",
                },
                HTTP_HOST="question-review.localhost",
            )
        self.assertEqual(
            log_event.call_args.kwargs["before"]["data"]["step6-custom"], original
        )
        self.assertEqual(
            log_event.call_args.kwargs["after"]["data"]["step6-custom"][0]["answer"],
            "A bridge",
        )
        self.assertEqual(response.status_code, 302)
        self.app.refresh_from_db()
        self.assertEqual(
            self.app.data["step6-custom"],
            [
                {**original[0], "answer": "A bridge"},
                {**original[1], "answer": "More ideas"},
            ],
        )
        self.assertEqual(self.app.data["_custom_question_program_id"], self.program.pk)

    def test_stale_or_incomplete_edit_does_not_overwrite_answers(self):
        edit = reverse("application_review_edit", args=[self.app.application_id])
        response = self.client.get(edit, HTTP_HOST="question-review.localhost")
        token = response.context["form"].initial["custom_snapshot_version"]
        original = self.app.data["step6-custom"]
        response = self.client.post(
            edit,
            {"custom_snapshot_version": token},
            HTTP_HOST="question-review.localhost",
        )
        self.assertContains(response, "Submit all custom answer fields")
        self.app.data["step6-custom"][0]["answer"] = "Newer answer"
        self.app.save()
        response = self.client.post(
            edit,
            {
                "custom_snapshot_version": token,
                f"custom_answer_{original[0]['question_id']}": "Stale answer",
                f"custom_answer_{original[1]['question_id']}": "",
            },
            HTTP_HOST="question-review.localhost",
        )
        self.assertContains(response, "application changed")
        self.app.refresh_from_db()
        self.assertEqual(self.app.data["step6-custom"][0]["answer"], "Newer answer")

    def test_required_answers_cannot_be_cleared_and_version_cannot_be_forged(self):
        self.app.data["step6-custom"][0]["required"] = True
        self.app.save()
        edit = reverse("application_review_edit", args=[self.app.application_id])
        response = self.client.get(edit, HTTP_HOST="question-review.localhost")
        token = response.context["form"].initial["custom_snapshot_version"]
        original = self.app.data["step6-custom"]
        payload = {
            "custom_snapshot_version": token,
            f"custom_answer_{original[0]['question_id']}": "",
            f"custom_answer_{original[1]['question_id']}": "",
        }
        response = self.client.post(
            edit, payload, HTTP_HOST="question-review.localhost"
        )
        self.assertContains(response, "This field is required")
        payload[f"custom_answer_{original[0]['question_id']}"] = "Valid"
        payload["custom_snapshot_version"] = "forged"
        response = self.client.post(
            edit, payload, HTTP_HOST="question-review.localhost"
        )
        self.assertContains(response, "application changed")
        self.app.refresh_from_db()
        self.assertEqual(self.app.data["step6-custom"], original)

    def test_non_lead_and_foreign_lead_cannot_edit_custom_answers(self):
        edit = reverse("application_review_edit", args=[self.app.application_id])
        original = self.app.data
        self.membership.role = "Mentor"
        self.membership.save()
        response = self.client.post(edit, {}, HTTP_HOST="question-review.localhost")
        if response.status_code == 302:
            self.assertNotEqual(response.url, self.url)
        else:
            self.assertEqual(response.status_code, 403)
        UserOrganizationMembership.objects.create(
            organization=self.other, user=self.lead, role="LeadMentor", is_active=True
        )
        response = self.client.post(
            edit, {}, HTTP_HOST="question-review-other.localhost"
        )
        if response.status_code == 302:
            self.assertNotEqual(response.url, self.url)
        else:
            self.assertIn(response.status_code, (403, 404))
        self.app.refresh_from_db()
        self.assertEqual(self.app.data, original)

    def test_converted_and_in_progress_answers_are_not_editable(self):
        edit = reverse("application_review_edit", args=[self.app.application_id])
        for status in (Application.Status.CONVERTED, Application.Status.EMAIL_VERIFIED):
            with self.subTest(status=status):
                self.app.status = status
                self.app.save()
                response = self.client.get(edit, HTTP_HOST="question-review.localhost")
                self.assertContains(response, "Custom answers cannot be edited")
                self.assertNotContains(response, "custom_answer_")
                version = response.context["form"].initial["custom_snapshot_version"]
                original = self.app.data["step6-custom"]
                response = self.client.post(
                    edit,
                    {
                        "custom_snapshot_version": version,
                        f"custom_answer_{original[0]['question_id']}": "Tampered",
                    },
                    HTTP_HOST="question-review.localhost",
                )
                self.assertEqual(response.status_code, 302)
                self.app.refresh_from_db()
                self.assertEqual(self.app.data["step6-custom"], original)

    def test_edit_page_open_at_conversion_cannot_report_success(self):
        edit = reverse("application_review_edit", args=[self.app.application_id])
        response = self.client.get(edit, HTTP_HOST="question-review.localhost")
        token = response.context["form"].initial["custom_snapshot_version"]
        original = self.app.data["step6-custom"]
        self.app.status = Application.Status.CONVERTED
        self.app.save()
        response = self.client.post(
            edit,
            {
                "custom_snapshot_version": token,
                f"custom_answer_{original[0]['question_id']}": "Stale edit",
                f"custom_answer_{original[1]['question_id']}": "",
            },
            HTTP_HOST="question-review.localhost",
        )
        self.assertContains(response, "application changed")
        self.app.refresh_from_db()
        self.assertEqual(self.app.data["step6-custom"], original)

    def test_malformed_snapshot_blocks_edit_without_normalizing_data(self):
        self.app.data["step6-custom"] = [{"question_text": "Bad", "answer": []}]
        self.app.data["step6"] = {"interest_reason": "Legacy response"}
        self.app.save()
        edit = reverse("application_review_edit", args=[self.app.application_id])
        get_response = self.client.get(edit, HTTP_HOST="question-review.localhost")
        version = get_response.context["form"].initial["custom_snapshot_version"]
        self.app.refresh_from_db()
        original = self.app.data.copy()
        response = self.client.post(
            edit,
            {
                "custom_snapshot_version": version,
                "email": "new@example.test",
            },
            HTTP_HOST="question-review.localhost",
        )
        self.assertContains(response, "Custom application questions are malformed")
        self.app.refresh_from_db()
        self.assertEqual(self.app.data, original)
        self.assertNotEqual(self.app.email, "new@example.test")
