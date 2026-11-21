from django.http import Http404
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from applications.models import Application, ProgramApplicationQuestion
from applications.tests.access_helpers import verify_application_browser
from organizations.models import Organization
from programs.models import Program


class ApplicantCustomQuestionTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(
            name="Applicant Questions",
            slug="applicant-questions",
            subdomain="applicant-questions",
        )
        self.program = Program.objects.create(
            organization=self.organization, name="Question Program"
        )
        self.question = ProgramApplicationQuestion.objects.create(
            organization=self.organization,
            program=self.program,
            text="Why do you want to join?",
            is_required=True,
            display_order=1,
        )
        self.application = self.make_application(current_step=6)
        self.client.defaults["HTTP_HOST"] = "applicant-questions.localhost"
        verify_application_browser(self.client, self.application)

    def make_application(self, current_step, data=None):
        return Application.objects.create(
            organization=self.organization,
            program=self.program,
            applicant_type=Application.Type.PARENT,
            email="parent@example.test",
            current_step=current_step,
            email_verified_at=timezone.now(),
            status=Application.Status.EMAIL_VERIFIED,
            data=data or {},
        )

    def url(self, name, application=None):
        return reverse(
            name,
            kwargs={"app_id": (application or self.application).application_id},
        )

    def test_step6_freezes_and_renders_program_question_wording(self):
        response = self.client.get(self.url("apply_step6"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Why do you want to join?")
        self.assertContains(response, f'name="question_{self.question.question_id}"')
        self.assertContains(response, 'name="experience_version"')
        self.application.refresh_from_db()
        self.assertEqual(
            self.application.data["step6-custom"][0]["question_text"],
            "Why do you want to join?",
        )
        self.assertEqual(
            self.application.data["_custom_question_program_id"], self.program.pk
        )

    def test_step6_prefills_legacy_experience_answers(self):
        legacy_application = self.make_application(
            current_step=6,
            data={"step6": {"interest_reason": "Saved legacy response"}},
        )
        verify_application_browser(self.client, legacy_application)

        response = self.client.get(self.url("apply_step6", legacy_application))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["form"]["interest_reason"].value(),
            "Saved legacy response",
        )
        legacy_application.refresh_from_db()
        self.assertEqual(
            legacy_application.data["step6"]["interest_reason"],
            "Saved legacy response",
        )

    def test_step6_keeps_frozen_questions_after_configuration_changes(self):
        first_response = self.client.get(self.url("apply_step6"))
        self.assertEqual(first_response.status_code, 200)
        self.question.text = "Changed after freezing?"
        self.question.is_required = False
        self.question.is_active = False
        self.question.save(update_fields=["text", "is_required", "is_active"])
        ProgramApplicationQuestion.objects.create(
            organization=self.organization,
            program=self.program,
            text="Added after freezing?",
        )

        response = self.client.get(self.url("apply_step6"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Why do you want to join?")
        self.assertNotContains(response, "Changed after freezing?")
        self.assertNotContains(response, "Added after freezing?")
        self.assertTrue(
            response.context["form"]
            .fields[f"question_{self.question.question_id}"]
            .required
        )

    def test_step6_saves_required_custom_answer_with_fixed_answers(self):
        get_response = self.client.get(self.url("apply_step6"))
        version = get_response.context["form"].initial["experience_version"]

        response = self.client.post(
            self.url("apply_step6"),
            {
                "experience_version": version,
                "interest_reason": "Robotics is exciting",
                f"question_{self.question.question_id}": "I want to learn robotics.",
            },
        )

        self.assertRedirects(
            response,
            reverse("apply_step7", kwargs={"app_id": self.application.application_id}),
            fetch_redirect_response=False,
        )
        self.application.refresh_from_db()
        self.assertEqual(
            self.application.data["step6-experience"]["interest_reason"],
            "Robotics is exciting",
        )
        self.assertEqual(
            self.application.data["step6-custom"][0]["answer"],
            "I want to learn robotics.",
        )

    def test_step6_rejects_missing_required_answer_without_advancing(self):
        get_response = self.client.get(self.url("apply_step6"))
        version = get_response.context["form"].initial["experience_version"]

        response = self.client.post(
            self.url("apply_step6"), {"experience_version": version}
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This field is required")
        self.application.refresh_from_db()
        self.assertEqual(self.application.current_step, 6)
        self.assertEqual(self.application.data["step6-custom"][0]["answer"], "")

    def test_step6_preserves_unrelated_intervening_write(self):
        get_response = self.client.get(self.url("apply_step6"))
        version = get_response.context["form"].initial["experience_version"]
        self.application.data["another-step"] = {"answer": "newer"}
        self.application.save(update_fields=["data"])

        response = self.client.post(
            self.url("apply_step6"),
            {
                "experience_version": version,
                f"question_{self.question.question_id}": "Stale answer",
            },
        )

        self.assertRedirects(
            response,
            reverse("apply_step7", kwargs={"app_id": self.application.application_id}),
            fetch_redirect_response=False,
        )
        self.application.refresh_from_db()
        self.assertEqual(self.application.data["another-step"]["answer"], "newer")
        self.assertEqual(
            self.application.data["step6-custom"][0]["answer"], "Stale answer"
        )
        self.assertEqual(self.application.current_step, 7)

    def test_step6_rejects_competing_answer_update(self):
        get_response = self.client.get(self.url("apply_step6"))
        version = get_response.context["form"].initial["experience_version"]
        self.application.refresh_from_db()
        self.application.data["step6-custom"][0]["answer"] = "Newer answer"
        self.application.save(update_fields=["data"])

        response = self.client.post(
            self.url("apply_step6"),
            {
                "experience_version": version,
                f"question_{self.question.question_id}": "Stale answer",
            },
        )

        self.assertEqual(response.status_code, 200, response.get("Location"))
        self.assertContains(response, "answers changed")
        self.application.refresh_from_db()
        self.assertEqual(
            self.application.data["step6-custom"][0]["answer"], "Newer answer"
        )
        self.assertEqual(self.application.current_step, 6)

    def test_optional_custom_question_can_be_left_blank(self):
        self.question.is_required = False
        self.question.save(update_fields=["is_required"])
        get_response = self.client.get(self.url("apply_step6"))
        version = get_response.context["form"].initial["experience_version"]

        response = self.client.post(
            self.url("apply_step6"), {"experience_version": version}
        )

        self.assertRedirects(
            response,
            reverse("apply_step7", kwargs={"app_id": self.application.application_id}),
            fetch_redirect_response=False,
        )
        self.application.refresh_from_db()
        self.assertEqual(self.application.data["step6-custom"][0]["answer"], "")

    def test_confirmation_displays_frozen_question_and_answer(self):
        self.application.current_step = 9
        self.application.data = {
            "step6-custom": [
                {
                    "question_id": str(self.question.question_id),
                    "question_text": "Wording preserved from application start",
                    "required": True,
                    "answer": "I want to build robots.",
                }
            ],
            "_custom_question_program_id": self.program.pk,
        }
        self.application.save(update_fields=["current_step", "data"])

        response = self.client.get(self.url("apply_step9"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Wording preserved from application start")
        self.assertContains(response, "I want to build robots.")

    def test_direct_confirmation_cannot_bypass_unfrozen_questions(self):
        self.application.current_step = 9
        self.application.save(update_fields=["current_step"])

        get_response = self.client.get(self.url("apply_step9"))
        post_response = self.client.post(self.url("apply_step9"), {"confirm": "on"})

        expected = reverse(
            "apply_step6", kwargs={"app_id": self.application.application_id}
        )
        self.assertRedirects(get_response, expected, fetch_redirect_response=False)
        self.assertRedirects(post_response, expected, fetch_redirect_response=False)
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, Application.Status.EMAIL_VERIFIED)

    def test_confirmation_cannot_submit_unanswered_required_question(self):
        self.application.current_step = 9
        self.application.data = {
            "step6-custom": [
                {
                    "question_id": str(self.question.question_id),
                    "question_text": "Required question wording",
                    "required": True,
                    "answer": "",
                }
            ],
            "_custom_question_program_id": self.program.pk,
            "step7-primaryparent": {"email_updates": True},
        }
        self.application.save(update_fields=["current_step", "data"])

        response = self.client.post(self.url("apply_step9"), {"confirm": "on"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Required custom questions are unanswered")
        self.assertContains(response, "Required question wording")
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, Application.Status.EMAIL_VERIFIED)

    def test_foreign_organization_application_is_not_visible(self):
        foreign_organization = Organization.objects.create(
            name="Other Applicant Questions",
            slug="other-applicant-questions",
            subdomain="other-applicant-questions",
        )
        foreign_program = Program.objects.create(
            organization=foreign_organization, name="Foreign Program"
        )
        foreign_application = Application.objects.create(
            organization=foreign_organization,
            program=foreign_program,
            applicant_type=Application.Type.PARENT,
            email="other-parent@example.test",
            current_step=6,
            email_verified_at=timezone.now(),
            status=Application.Status.EMAIL_VERIFIED,
        )

        request = RequestFactory().get(self.url("apply_step6", foreign_application))
        request.organization = self.organization
        request.session = {}

        from applications.views.steps_student import Step6ExperienceView

        with self.assertRaises(Http404):
            Step6ExperienceView.as_view()(
                request, app_id=foreign_application.application_id
            )
