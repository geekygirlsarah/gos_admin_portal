from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from applications.models import ProgramApplicationQuestion
from organizations.models import Organization, UserOrganizationMembership
from programs.models import Program


class QuestionConfigurationTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            name="Questions", slug="questions", subdomain="questions"
        )
        self.other = Organization.objects.create(
            name="Other Questions", slug="other-questions", subdomain="other-questions"
        )
        self.program = Program.objects.create(organization=self.org, name="Program")
        self.other_program = Program.objects.create(
            organization=self.other, name="Other program"
        )
        self.lead = get_user_model().objects.create_user(username="question-lead")
        self.membership = UserOrganizationMembership.objects.create(
            user=self.lead, organization=self.org, role="LeadMentor", is_active=True
        )
        self.client.force_login(self.lead)
        self.host = {"HTTP_HOST": "questions.localhost"}

    def create_url(self, program=None):
        return reverse(
            "program_application_question_create", args=[(program or self.program).pk]
        )

    def test_lead_can_add_edit_and_deactivate_question_from_settings(self):
        response = self.client.post(
            self.create_url(),
            {
                "text": "What do you like?",
                "is_required": "on",
                "is_active": "on",
                "display_order": 2,
            },
            **self.host,
        )
        self.assertEqual(response.status_code, 302)
        question = ProgramApplicationQuestion.objects.get()
        self.assertEqual(question.organization, self.org)
        self.assertEqual(question.program, self.program)
        self.assertTrue(question.is_required)
        response = self.client.get(
            reverse("program_edit", args=[self.program.pk]), **self.host
        )
        self.assertContains(response, "What do you like?")
        self.assertContains(response, "not yet shown to applicants")
        edit = reverse(
            "program_application_question_edit", args=[self.program.pk, question.pk]
        )
        response = self.client.post(
            edit, {"text": "What would you build?", "display_order": 1}, **self.host
        )
        self.assertEqual(response.status_code, 302)
        question.refresh_from_db()
        self.assertEqual(question.text, "What would you build?")
        self.assertFalse(question.is_active)
        self.assertFalse(question.is_required)

    def test_mentor_cannot_manage_questions(self):
        self.membership.role = "Mentor"
        self.membership.save()
        response = self.client.post(
            self.create_url(), {"text": "No", "display_order": 0}, **self.host
        )
        self.assertNotEqual(response.status_code, 200)
        self.assertFalse(ProgramApplicationQuestion.objects.exists())

    def test_lead_cannot_manage_foreign_program_or_question(self):
        response = self.client.get(self.create_url(self.other_program), **self.host)
        self.assertNotEqual(response.status_code, 200)
        self.assertNotContains(
            response, "Other program", status_code=response.status_code
        )
        self.client.post(
            self.create_url(self.other_program),
            {"text": "Forbidden", "display_order": 0},
            **self.host,
        )
        self.assertFalse(ProgramApplicationQuestion.objects.exists())
        question = ProgramApplicationQuestion.objects.create(
            organization=self.other, program=self.other_program, text="Private"
        )
        url = reverse(
            "program_application_question_edit", args=[self.program.pk, question.pk]
        )
        response = self.client.get(url, **self.host)
        self.assertNotEqual(response.status_code, 200)
        self.assertNotContains(response, "Private", status_code=response.status_code)
        self.client.post(url, {"text": "Changed", "display_order": 0}, **self.host)
        question.refresh_from_db()
        self.assertEqual(question.text, "Private")

    def test_empty_question_is_not_saved(self):
        response = self.client.post(
            self.create_url(), {"text": "   ", "display_order": 0}, **self.host
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(ProgramApplicationQuestion.objects.exists())

    def test_program_and_organization_must_match(self):
        question = ProgramApplicationQuestion(
            organization=self.other, program=self.program, text="Question"
        )
        with self.assertRaises(ValidationError):
            question.full_clean()

    def test_settings_only_lists_owned_questions_in_order(self):
        later = ProgramApplicationQuestion.objects.create(
            organization=self.org, program=self.program, text="Later", display_order=5
        )
        earlier = ProgramApplicationQuestion.objects.create(
            organization=self.org, program=self.program, text="Earlier", display_order=1
        )
        ProgramApplicationQuestion.objects.create(
            organization=self.other, program=self.other_program, text="Other question"
        )
        response = self.client.get(
            reverse("program_edit", args=[self.program.pk]), **self.host
        )
        self.assertEqual(
            list(response.context["application_questions"]), [earlier, later]
        )
        self.assertNotContains(response, "Other question")
