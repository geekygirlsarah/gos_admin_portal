from uuid import uuid4

from cryptography.fernet import Fernet
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import TextField
from django.db.models.functions import Cast
from django.test import TestCase, override_settings

from applications.models import (
    Application,
    ApplicationFixedAnswer,
    ApplicationGuardianDraft,
    ApplicationQuestionAnswer,
    ApplicationQuestionSet,
    ApplicationStudentDraft,
    ApplicationStudentDraftRaceEthnicity,
    ProgramApplicationQuestion,
)
from organizations.models import Organization
from programs.models import Program, RaceEthnicity

_FILE_ENCRYPTION_KEY = Fernet.generate_key().decode()


class StructuredApplicationDraftModelTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(
            name="Draft Organization",
            slug="structured-draft",
            subdomain="structured-draft",
        )
        self.other_organization = Organization.objects.create(
            name="Other Draft Organization",
            slug="structured-draft-other",
            subdomain="structured-draft-other",
        )
        self.program = Program.objects.create(
            organization=self.organization,
            name="Draft Program",
        )
        self.application = Application.objects.create(
            organization=self.organization,
            program=self.program,
        )
        self.race = RaceEthnicity.objects.create(
            organization=self.organization,
            key="draft-race",
            name="Draft race",
        )
        self.other_race = RaceEthnicity.objects.create(
            organization=self.other_organization,
            key="other-draft-race",
            name="Other draft race",
        )

    def test_student_draft_represents_unanswered_and_explicit_values(self):
        draft = ApplicationStudentDraft.objects.create(
            application=self.application,
            organization=self.organization,
            legal_first_name="",
            confirm_age=False,
            school_name="",
            school_not_listed=True,
            allergies="",
            allergies_answered=True,
        )

        draft.refresh_from_db()

        self.assertIsNone(draft.last_name)
        self.assertEqual(draft.legal_first_name, "")
        self.assertIsNone(draft.confirm_grade)
        self.assertFalse(draft.confirm_age)
        self.assertEqual(draft.school_name, "")
        self.assertTrue(draft.school_not_listed)
        self.assertIsNone(draft.allergies)
        self.assertTrue(draft.allergies_answered)

    @override_settings(FILE_ENCRYPTION_KEY=_FILE_ENCRYPTION_KEY)
    def test_student_draft_medical_text_is_encrypted_in_database(self):
        plaintext = "Sensitive medical detail"
        draft = ApplicationStudentDraft.objects.create(
            application=self.application,
            organization=self.organization,
            allergies=plaintext,
            allergies_answered=True,
        )

        stored_value = (
            ApplicationStudentDraft.objects.filter(pk=draft.pk)
            .annotate(stored_value=Cast("allergies", output_field=TextField()))
            .values_list("stored_value", flat=True)
            .get()
        )

        draft.refresh_from_db()
        self.assertNotIn(plaintext, stored_value)
        self.assertEqual(draft.allergies, plaintext)

    def test_draft_managers_scope_by_organization(self):
        own = ApplicationStudentDraft.objects.create(
            application=self.application,
            organization=self.organization,
        )
        other_application = Application.objects.create(
            organization=self.other_organization,
        )
        foreign = ApplicationStudentDraft.objects.create(
            application=other_application,
            organization=self.other_organization,
        )

        self.assertQuerySetEqual(
            ApplicationStudentDraft.objects.for_organization(self.organization),
            [own],
        )
        self.assertNotIn(
            foreign,
            ApplicationStudentDraft.objects.for_organization(self.organization),
        )

    def test_student_draft_rejects_application_from_another_organization(self):
        draft = ApplicationStudentDraft(
            application=self.application,
            organization=self.other_organization,
        )

        with self.assertRaises(ValidationError):
            draft.save()

    def test_race_ethnicity_link_rejects_cross_organization_record(self):
        draft = ApplicationStudentDraft.objects.create(
            application=self.application,
            organization=self.organization,
        )

        with self.assertRaises(ValidationError):
            ApplicationStudentDraftRaceEthnicity.objects.create(
                organization=self.organization,
                student_draft=draft,
                race_ethnicity=self.other_race,
            )

        link = ApplicationStudentDraftRaceEthnicity.objects.create(
            organization=self.organization,
            student_draft=draft,
            race_ethnicity=self.race,
        )
        self.assertEqual(link.race_ethnicity, self.race)

    def test_guardian_slots_and_presence_states_are_structured(self):
        guardian = ApplicationGuardianDraft.objects.create(
            application=self.application,
            organization=self.organization,
            slot=ApplicationGuardianDraft.Slot.SECONDARY,
            presence=ApplicationGuardianDraft.Presence.SKIPPED,
        )
        self.assertEqual(guardian.presence, ApplicationGuardianDraft.Presence.SKIPPED)
        self.assertIsNone(guardian.email)

        ApplicationGuardianDraft.objects.create(
            application=self.application,
            organization=self.organization,
            slot=ApplicationGuardianDraft.Slot.PRIMARY,
            presence=ApplicationGuardianDraft.Presence.PRESENT,
            email="guardian@example.test",
            can_receive_texts=False,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            ApplicationGuardianDraft.objects.create(
                application=self.application,
                organization=self.organization,
                slot=ApplicationGuardianDraft.Slot.PRIMARY,
            )

    def test_guardian_draft_rejects_application_from_another_organization(self):
        guardian = ApplicationGuardianDraft(
            application=self.application,
            organization=self.other_organization,
            slot=ApplicationGuardianDraft.Slot.PRIMARY,
        )

        with self.assertRaises(ValidationError):
            guardian.save()

    def test_question_set_and_ordered_answers_preserve_frozen_wording(self):
        source = ProgramApplicationQuestion.objects.create(
            organization=self.organization,
            program=self.program,
            text="Original wording?",
            is_required=True,
        )
        question_set = ApplicationQuestionSet.objects.create(
            application=self.application,
            organization=self.organization,
            program=self.program,
        )
        later = ApplicationQuestionAnswer.objects.create(
            organization=self.organization,
            question_set=question_set,
            position=1,
            question_id=source.question_id,
            question_text=source.text,
            is_required=source.is_required,
            answer="Saved response",
        )
        earlier = ApplicationQuestionAnswer.objects.create(
            organization=self.organization,
            question_set=question_set,
            position=0,
            question_id=uuid4(),
            question_text="Earlier question?",
            is_required=False,
            answer=None,
        )

        source.text = "Changed live wording?"
        source.is_required = False
        source.save()

        answers = list(question_set.answers.all())
        self.assertEqual(answers, [earlier, later])
        self.assertEqual(later.question_text, "Original wording?")
        self.assertTrue(later.is_required)
        self.assertEqual(later.answer, "Saved response")
        self.assertIsNone(earlier.answer)

    def test_empty_question_set_is_distinct_from_no_frozen_set(self):
        empty_set = ApplicationQuestionSet.objects.create(
            application=self.application,
            organization=self.organization,
            program=self.program,
        )
        second_program = Program.objects.create(
            organization=self.organization,
            name="Second program",
        )

        self.assertTrue(
            ApplicationQuestionSet.objects.filter(
                application=self.application,
                program=self.program,
            ).exists()
        )
        self.assertEqual(empty_set.answers.count(), 0)
        self.assertFalse(
            ApplicationQuestionSet.objects.filter(
                application=self.application,
                program=second_program,
            ).exists()
        )

    def test_question_sets_are_tenant_scoped_and_validate_program_ownership(self):
        own_set = ApplicationQuestionSet.objects.create(
            application=self.application,
            organization=self.organization,
            program=self.program,
        )
        other_program = Program.objects.create(
            organization=self.other_organization,
            name="Foreign program",
        )
        other_application = Application.objects.create(
            organization=self.other_organization,
            program=other_program,
        )

        self.assertQuerySetEqual(
            ApplicationQuestionSet.objects.for_organization(self.organization),
            [own_set],
        )
        with self.assertRaises(ValidationError):
            ApplicationQuestionSet.objects.create(
                application=self.application,
                organization=self.organization,
                program=other_program,
            )
        with self.assertRaises(ValidationError):
            ApplicationQuestionSet.objects.create(
                application=other_application,
                organization=self.organization,
                program=self.program,
            )

    def test_question_answers_reject_cross_tenant_links_and_invalid_ids(self):
        question_set = ApplicationQuestionSet.objects.create(
            application=self.application,
            organization=self.organization,
            program=self.program,
        )

        with self.assertRaises(ValidationError):
            ApplicationQuestionAnswer.objects.create(
                organization=self.other_organization,
                question_set=question_set,
                position=0,
                question_id=uuid4(),
                question_text="Foreign answer?",
                is_required=False,
            )

        malformed = ApplicationQuestionAnswer(
            organization=self.organization,
            question_set=question_set,
            position=0,
            question_id="not-a-uuid",
            question_text="Malformed ID?",
            is_required=False,
        )
        with self.assertRaises(ValidationError):
            malformed.full_clean()

    def test_question_set_and_answer_cardinality_constraints(self):
        question_set = ApplicationQuestionSet.objects.create(
            application=self.application,
            organization=self.organization,
            program=self.program,
        )
        question_id = uuid4()
        ApplicationQuestionAnswer.objects.create(
            organization=self.organization,
            question_set=question_set,
            position=0,
            question_id=question_id,
            question_text="First?",
            is_required=False,
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            ApplicationQuestionSet.objects.create(
                application=self.application,
                organization=self.organization,
                program=self.program,
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            ApplicationQuestionAnswer.objects.create(
                organization=self.organization,
                question_set=question_set,
                position=1,
                question_id=question_id,
                question_text="Duplicate identifier?",
                is_required=False,
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            ApplicationQuestionAnswer.objects.create(
                organization=self.organization,
                question_set=question_set,
                position=0,
                question_id=uuid4(),
                question_text="Duplicate position?",
                is_required=False,
            )

    def test_fixed_answers_keep_legacy_and_current_conflicts(self):
        legacy = ApplicationFixedAnswer.objects.create(
            organization=self.organization,
            application=self.application,
            source_step="step6",
            question_key="interest_reason",
            answer="Legacy response",
        )
        current = ApplicationFixedAnswer.objects.create(
            organization=self.organization,
            application=self.application,
            source_step="step6-experience",
            question_key="interest_reason",
            answer="Current response",
        )
        blank = ApplicationFixedAnswer.objects.create(
            organization=self.organization,
            application=self.application,
            source_step="step6-experience",
            question_key="referral_source",
            answer="",
        )
        unanswered = ApplicationFixedAnswer.objects.create(
            organization=self.organization,
            application=self.application,
            source_step="step6-experience",
            question_key="prior_robotics_experience",
            answer=None,
        )

        self.assertEqual(legacy.answer, "Legacy response")
        self.assertEqual(current.answer, "Current response")
        self.assertEqual(blank.answer, "")
        self.assertIsNone(unanswered.answer)

    def test_fixed_answer_rejects_cross_organization_application(self):
        answer = ApplicationFixedAnswer(
            organization=self.other_organization,
            application=self.application,
            source_step="step6",
            question_key="interest_reason",
        )

        with self.assertRaises(ValidationError):
            answer.save()
