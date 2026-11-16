from importlib import import_module
from io import StringIO
from types import SimpleNamespace
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from applications.models import Application, StudentApplicationAnswer
from applications.services import (
    ApplicationConversionError,
    convert_application_to_student,
)
from organizations.models import Organization, UserOrganizationMembership
from programs.models import Adult, AdultStudentRelationship, Program, Student
from programs.utils.merges import perform_student_merge


class StudentApplicationAnswerTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            name="Answer Org", slug="answer-org", subdomain="answer-org"
        )
        self.other = Organization.objects.create(
            name="Other Org", slug="answer-other", subdomain="answer-other"
        )
        self.program = Program.objects.create(organization=self.org, name="Program")
        self.student = Student.objects.create(
            organization=self.org,
            legal_first_name="Test",
            last_name="Student",
            date_of_birth="2010-01-01",
        )

    def application(self, **kwargs):
        defaults = {
            "organization": self.org,
            "program": self.program,
            "applicant_type": Application.Type.PARENT,
            "status": Application.Status.CONVERTED,
            "converted_student": self.student,
            "data": {"step6-experience": {"referral_source": "A friend"}},
        }
        defaults.update(kwargs)
        return Application.objects.create(**defaults)

    def run_backfill(self):
        migration = import_module(
            "applications.migrations.0020_backfill_student_answers"
        )
        historical_apps = (
            MigrationExecutor(connection)
            .loader.project_state([("applications", "0019_studentapplicationanswer")])
            .apps
        )
        migration.backfill_answers(
            historical_apps, SimpleNamespace(connection=connection)
        )

    def test_reverse_backfill_preserves_answers_and_allows_replay(self):
        application = self.application()
        self.run_backfill()
        answer = StudentApplicationAnswer.objects.get(application=application)
        migration = import_module(
            "applications.migrations.0020_backfill_student_answers"
        )
        operation = migration.Migration.operations[0]
        self.assertTrue(operation.reversible)
        historical_apps = (
            MigrationExecutor(connection)
            .loader.project_state([("applications", "0019_studentapplicationanswer")])
            .apps
        )
        operation.reverse_code(historical_apps, SimpleNamespace(connection=connection))
        answer.refresh_from_db()
        self.assertEqual(answer.answer, "A friend")
        self.run_backfill()
        self.assertEqual(
            StudentApplicationAnswer.objects.filter(application=application).count(), 1
        )

    def test_backfill_keeps_each_application_and_both_step_versions(self):
        first = self.application(
            data={
                "step6": {
                    "interest_reason": "Old answer",
                    "referral_source": "Friend",
                    "prior_robotics_experience": "None",
                },
                "step6-experience": {
                    "interest_reason": "New answer",
                    "hoped_gains": "Skills",
                    "referral_source": "Friend",
                },
            }
        )
        second = self.application(data={"step6": {"referral_source": "School"}})
        self.run_backfill()
        self.run_backfill()
        answers = StudentApplicationAnswer.objects.filter(student=self.student)
        self.assertEqual(answers.count(), 6)
        self.assertEqual(
            set(answers.filter(application=first).values_list("answer", flat=True)),
            {"Old answer", "New answer", "Skills", "Friend", "None"},
        )
        self.assertEqual(
            answers.get(
                application=first,
                source_step="step6-experience",
                question_key="hoped_gains",
            ).question_text,
            "What does your child hope to gain from the experience?",
        )
        self.assertEqual(answers.get(application=second).answer, "School")
        first.refresh_from_db()
        self.assertEqual(first.data["step6"]["interest_reason"], "Old answer")

    def test_backfill_only_uses_explicit_consistent_student_links(self):
        approved = self.application(status=Application.Status.APPROVED)
        signed = self.application(status=Application.Status.APPROVED_SIGNED)
        self.application(converted_student=None)
        self.application(status=Application.Status.SUBMITTED)
        self.application(applicant_type=Application.Type.MENTOR)
        foreign = Student.objects.create(
            organization=self.other, legal_first_name="Other", last_name="Student"
        )
        self.application(converted_student=foreign)
        foreign_program = Program.objects.create(organization=self.other, name="Other")
        self.application(program=foreign_program)
        self.application(data={"step6-experience": {"referral_source": ["bad"]}})
        self.application(data={"step6-experience": "bad"})
        self.run_backfill()
        self.assertEqual(
            set(
                StudentApplicationAnswer.objects.values_list(
                    "application_id", flat=True
                )
            ),
            {approved.pk, signed.pk},
        )

    def test_conversion_saves_answers_before_legacy_normalization(self):
        application = self.application(
            status=Application.Status.APPROVED_SIGNED,
            converted_student=None,
            data={
                "step5-student": {
                    "legal_first_name": "New",
                    "last_name": "Student",
                    "date_of_birth": "2011-01-01",
                },
                "step6": {"referral_source": "Old response"},
                "step6-experience": {"referral_source": "New response"},
            },
        )
        student = convert_application_to_student(application)
        self.assertEqual(
            set(student.application_answers.values_list("answer", flat=True)),
            {"Old response", "New response"},
        )

    def test_failed_conversion_keeps_original_step_data(self):
        data = {
            "step6": {"referral_source": "Old response"},
            "step6-experience": {"referral_source": "New response"},
        }
        application = self.application(
            status=Application.Status.APPROVED_SIGNED,
            converted_student=None,
            data=data,
        )
        with self.assertRaises(ApplicationConversionError):
            convert_application_to_student(application)
        application.refresh_from_db()
        self.assertEqual(application.data, data)
        self.assertFalse(
            StudentApplicationAnswer.objects.filter(application=application).exists()
        )

    def test_conversion_rejects_malformed_answers_without_losing_them(self):
        data = {
            "step5-student": {
                "legal_first_name": "New",
                "last_name": "Student",
            },
            "step6": {"referral_source": ["bad"]},
            "step6-experience": {"referral_source": "Valid"},
        }
        application = self.application(
            status=Application.Status.APPROVED_SIGNED,
            converted_student=None,
            data=data,
        )
        with self.assertRaisesMessage(
            ApplicationConversionError, "An application experience answer is malformed."
        ):
            convert_application_to_student(application)
        application.refresh_from_db()
        self.assertEqual(application.data, data)
        self.assertFalse(
            StudentApplicationAnswer.objects.filter(application=application).exists()
        )

    def test_conversion_preserves_custom_question_snapshot_and_legacy_answers(self):
        question_id = str(uuid4())
        application = self.application(
            status=Application.Status.APPROVED_SIGNED,
            converted_student=None,
            data={
                "step5-student": {
                    "legal_first_name": "New",
                    "last_name": "Student",
                    "date_of_birth": "2011-01-01",
                },
                "step6-experience": {"referral_source": "A friend"},
                "step6-custom": [
                    {
                        "question_id": question_id,
                        "question_text": "What would you like to build?",
                        "answer": "A robot",
                    },
                    {
                        "question_id": str(uuid4()),
                        "question_text": "Anything else?",
                        "answer": "",
                    },
                ],
            },
        )
        student = convert_application_to_student(application)
        answer = student.application_answers.get(
            source_step="step6-custom", question_key=question_id
        )
        self.assertEqual(answer.question_text, "What would you like to build?")
        self.assertEqual(answer.answer, "A robot")
        self.assertEqual(student.application_answers.count(), 2)

    def test_conversion_rejects_duplicate_custom_questions_without_losing_data(self):
        question_id = str(uuid4())
        item = {
            "question_id": question_id,
            "question_text": "Question?",
            "answer": "Answer",
        }
        data = {
            "step5-student": {"legal_first_name": "New", "last_name": "Student"},
            "step6-custom": [item, item.copy()],
        }
        application = self.application(
            status=Application.Status.APPROVED_SIGNED,
            converted_student=None,
            data=data,
        )
        with self.assertRaisesMessage(
            ApplicationConversionError, "A custom application question is duplicated."
        ):
            convert_application_to_student(application)
        application.refresh_from_db()
        self.assertEqual(application.data, data)
        self.assertFalse(application.student_answers.exists())

    def test_report_lists_only_local_unlinked_and_inconsistent_applications(self):
        unlinked = self.application(converted_student=None)
        foreign_student = Student.objects.create(
            organization=self.other, legal_first_name="Other", last_name="Student"
        )
        inconsistent = self.application(converted_student=foreign_student)
        malformed = self.application(data={"step6": {"interest_reason": ["bad"]}})
        self.application()
        other_app = self.application(
            organization=self.other, program=None, converted_student=None
        )
        output = StringIO()
        call_command(
            "report_application_answer_backfill",
            organization=self.org.slug,
            stdout=output,
        )
        self.assertIn(
            f"{unlinked.application_id}: no linked student", output.getvalue()
        )
        self.assertIn(
            f"{inconsistent.application_id}: inconsistent ownership", output.getvalue()
        )
        self.assertIn(
            f"{malformed.application_id}: malformed answer data", output.getvalue()
        )
        self.assertNotIn(other_app.application_id, output.getvalue())
        self.assertIn("Applications requiring review: 3", output.getvalue())

    def test_report_can_identify_linked_answers_missing_from_backfill(self):
        missing = self.application()
        copied = self.application(data={"step6": {"referral_source": "School"}})
        duplicate = self.application(
            data={
                "step6": {"referral_source": "Same"},
                "step6-experience": {"referral_source": "Same"},
            }
        )
        moved = self.application(data={"step6": {"interest_reason": "Old step"}})
        self.application(data={"step6": {"referral_source": "  "}})
        foreign_student = Student.objects.create(
            organization=self.other, legal_first_name="Other", last_name="Student"
        )
        foreign = self.application(
            organization=self.other, program=None, converted_student=foreign_student
        )
        self.run_backfill()
        moved.data = {"step6-experience": {"interest_reason": "Old step"}}
        moved.save(update_fields=["data"])
        StudentApplicationAnswer.objects.filter(application=missing).delete()
        output = StringIO()
        call_command(
            "report_application_answer_backfill",
            organization=self.org.slug,
            check_copied=True,
            stdout=output,
        )
        self.assertIn(
            f"{missing.application_id}: missing copied answers", output.getvalue()
        )
        self.assertNotIn(copied.application_id, output.getvalue())
        self.assertNotIn(duplicate.application_id, output.getvalue())
        self.assertNotIn(moved.application_id, output.getvalue())
        self.assertNotIn(foreign.application_id, output.getvalue())
        self.assertNotIn("A friend", output.getvalue())
        self.assertIn("Applications requiring review: 1", output.getvalue())

    def test_lead_sees_empty_answer_card_but_mentor_does_not(self):
        user = get_user_model().objects.create_user(username="empty-answer-lead")
        membership = UserOrganizationMembership.objects.create(
            user=user, organization=self.org, role="LeadMentor", is_active=True
        )
        self.client.force_login(user)
        url = reverse("student_detail", args=[self.student.pk])
        response = self.client.get(url, HTTP_HOST="answer-org.localhost")
        self.assertContains(response, "No linked application answers on file")
        membership.role = "Mentor"
        membership.save()
        response = self.client.get(url, HTTP_HOST="answer-org.localhost")
        self.assertNotContains(response, "No linked application answers on file")

    def test_lead_can_see_existing_profile_responses_without_an_application(self):
        self.student.interest_reason = "Interested in building"
        self.student.referral_source = "A teacher"
        self.student.save()
        user = get_user_model().objects.create_user(username="legacy-answer-lead")
        membership = UserOrganizationMembership.objects.create(
            user=user, organization=self.org, role="LeadMentor", is_active=True
        )
        self.client.force_login(user)
        url = reverse("student_detail", args=[self.student.pk])
        response = self.client.get(url, HTTP_HOST="answer-org.localhost")
        self.assertContains(response, "Existing student profile responses")
        self.assertContains(response, "Interested in building")
        self.assertContains(response, "A teacher")
        membership.role = "Mentor"
        membership.save()
        response = self.client.get(url, HTTP_HOST="answer-org.localhost")
        self.assertNotContains(response, "Interested in building")
        self.assertNotContains(response, "A teacher")

    def test_report_flags_partial_or_inconsistently_owned_copies(self):
        partial = self.application(
            data={
                "step6-experience": {
                    "referral_source": "A teacher",
                    "interest_reason": "Building",
                }
            }
        )
        inconsistent = self.application()
        self.run_backfill()
        StudentApplicationAnswer.objects.filter(
            application=partial, question_key="interest_reason"
        ).delete()
        StudentApplicationAnswer.objects.filter(application=inconsistent).update(
            organization=self.other
        )
        output = StringIO()
        call_command(
            "report_application_answer_backfill",
            organization=self.org.slug,
            check_copied=True,
            stdout=output,
        )
        self.assertIn(
            f"{partial.application_id}: missing copied answers", output.getvalue()
        )
        self.assertIn(
            f"{inconsistent.application_id}: missing copied answers", output.getvalue()
        )
        self.assertNotIn("Building", output.getvalue())
        self.assertIn("Applications requiring review: 2", output.getvalue())

    def test_lead_can_view_answers_but_mentor_cannot(self):
        application = self.application()
        self.run_backfill()
        user = get_user_model().objects.create_user(username="answer-lead")
        membership = UserOrganizationMembership.objects.create(
            user=user, organization=self.org, role="LeadMentor", is_active=True
        )
        self.client.force_login(user)
        url = reverse("student_detail", args=[self.student.pk])
        response = self.client.get(url, HTTP_HOST="answer-org.localhost")
        self.assertContains(response, "A friend")
        self.assertContains(response, application.application_id)
        membership.role = "Mentor"
        membership.save()
        response = self.client.get(url, HTTP_HOST="answer-org.localhost")
        self.assertNotContains(response, "A friend")

    def test_profile_does_not_render_inconsistent_answer(self):
        application = self.application()
        StudentApplicationAnswer.objects.create(
            organization=self.other,
            student=self.student,
            application=application,
            source_step="step6-experience",
            question_key="referral_source",
            question_text="How did you hear?",
            answer="Foreign answer",
        )
        user = get_user_model().objects.create_user(username="answer-lead")
        UserOrganizationMembership.objects.create(
            user=user, organization=self.org, role="LeadMentor", is_active=True
        )
        self.client.force_login(user)
        response = self.client.get(
            reverse("student_detail", args=[self.student.pk]),
            HTTP_HOST="answer-org.localhost",
        )
        self.assertNotContains(response, "Foreign answer")

    def test_profile_answer_query_count_does_not_grow_per_application(self):
        user = get_user_model().objects.create_superuser(
            username="answer-admin", email="answer-admin@example.test", password="test"
        )  # nosec B106
        self.client.force_login(user)
        url = reverse("student_detail", args=[self.student.pk])
        self.application()
        self.run_backfill()
        self.client.get(url, HTTP_HOST="answer-org.localhost")
        with CaptureQueriesContext(connection) as one_answer:
            response = self.client.get(url, HTTP_HOST="answer-org.localhost")
        self.assertContains(response, "A friend")
        for _ in range(10):
            self.application()
        self.run_backfill()
        with CaptureQueriesContext(connection) as many_answers:
            response = self.client.get(url, HTTP_HOST="answer-org.localhost")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(one_answer), len(many_answers))

    def test_student_cannot_see_answers_and_foreign_lead_cannot_open_profile(self):
        self.application()
        self.run_backfill()
        user = get_user_model().objects.create_user(username="applicant-student")
        self.student.user = user
        self.student.interest_reason = "Private profile response"
        self.student.save()
        self.client.force_login(user)
        url = reverse("student_detail", args=[self.student.pk])
        response = self.client.get(url, HTTP_HOST="answer-org.localhost")
        self.assertNotContains(response, "A friend")
        self.assertNotContains(response, "Private profile response")

        foreign_lead = get_user_model().objects.create_user(username="foreign-lead")
        UserOrganizationMembership.objects.create(
            user=foreign_lead,
            organization=self.other,
            role="LeadMentor",
            is_active=True,
        )
        self.client.force_login(foreign_lead)
        response = self.client.get(url, HTTP_HOST="answer-other.localhost")
        self.assertRedirects(response, reverse("home"), fetch_redirect_response=False)

    def test_parent_can_open_profile_but_cannot_see_answers(self):
        self.application()
        self.run_backfill()
        self.student.interest_reason = "Private profile response"
        self.student.save()
        user = get_user_model().objects.create_user(username="applicant-parent")
        parent = Adult.objects.create(
            organization=self.org,
            user=user,
            is_parent=True,
            legal_first_name="Parent",
            last_name="Example",
        )
        AdultStudentRelationship.objects.create(
            organization=self.org,
            student=self.student,
            adult=parent,
            relationship_to_student="parent",
        )
        self.client.force_login(user)
        response = self.client.get(
            reverse("student_detail", args=[self.student.pk]),
            HTTP_HOST="answer-org.localhost",
        )
        self.assertNotContains(response, "A friend")
        self.assertNotContains(response, "Private profile response")

    def test_student_merge_preserves_answers_and_checks_ownership(self):
        source = Student.objects.create(
            organization=self.org, legal_first_name="Source", last_name="Student"
        )
        application = self.application(converted_student=source)
        self.run_backfill()
        perform_student_merge(self.student, source)
        answer = StudentApplicationAnswer.objects.get(application=application)
        self.assertEqual(answer.student_id, self.student.pk)
        application.refresh_from_db()
        self.assertEqual(application.converted_student_id, self.student.pk)

        source = Student.objects.create(
            organization=self.org, legal_first_name="Source", last_name="Student"
        )
        application = self.application(converted_student=source)
        StudentApplicationAnswer.objects.create(
            organization=self.other,
            student=source,
            application=application,
            source_step="step6-experience",
            question_key="referral_source",
            question_text="Question",
            answer="Answer",
        )
        with self.assertRaises(ValidationError):
            perform_student_merge(self.student, source)
