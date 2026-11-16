from django.test import TestCase

from applications.answers import (
    build_custom_question_snapshot,
    validate_custom_question_snapshot,
)
from applications.forms import CustomApplicationQuestionsForm
from applications.models import Application, ProgramApplicationQuestion
from organizations.models import Organization
from programs.models import Program


class CustomQuestionSnapshotTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            name="Snapshot Org", slug="snapshot-org", subdomain="snapshot-org"
        )
        self.other = Organization.objects.create(
            name="Other Snapshot", slug="other-snapshot", subdomain="other-snapshot"
        )
        self.program = Program.objects.create(organization=self.org, name="Program")
        self.app = Application.objects.create(
            organization=self.org, program=self.program
        )
        self.required = ProgramApplicationQuestion.objects.create(
            organization=self.org,
            program=self.program,
            text="Required question?",
            is_required=True,
            display_order=1,
        )
        self.optional = ProgramApplicationQuestion.objects.create(
            organization=self.org,
            program=self.program,
            text="Optional question?",
            display_order=2,
        )
        ProgramApplicationQuestion.objects.create(
            organization=self.org,
            program=self.program,
            text="Inactive",
            is_active=False,
        )
        ProgramApplicationQuestion.objects.create(
            organization=self.other, program=self.program, text="Foreign"
        )

    def test_snapshot_is_ordered_scoped_and_unaffected_by_changes(self):
        snapshot = build_custom_question_snapshot(self.app)
        self.assertEqual(
            [q["question_text"] for q in snapshot],
            ["Required question?", "Optional question?"],
        )
        self.required.text = "Changed?"
        self.required.is_active = False
        self.required.save()
        self.assertEqual(snapshot[0]["question_text"], "Required question?")
        self.assertTrue(snapshot[0]["required"])

    def test_form_enforces_required_and_preserves_snapshot(self):
        snapshot = build_custom_question_snapshot(self.app)
        form = CustomApplicationQuestionsForm({}, snapshot=snapshot)
        self.assertFalse(form.is_valid())
        key = str(self.required.question_id)
        form = CustomApplicationQuestionsForm(
            {f"question_{key}": "  A response  "}, snapshot=snapshot
        )
        self.assertTrue(form.is_valid(), form.errors)
        answered = form.answered_snapshot()
        self.assertEqual(answered[0]["answer"], "A response")
        self.assertEqual(answered[1]["answer"], "")
        self.assertEqual(snapshot[0]["answer"], "")
        validate_custom_question_snapshot(answered, require_answers=True)

    def test_missing_required_and_malformed_snapshot_are_rejected(self):
        snapshot = build_custom_question_snapshot(self.app)
        with self.assertRaisesMessage(
            ValueError, "Required custom questions are unanswered"
        ):
            validate_custom_question_snapshot(snapshot, require_answers=True)
        snapshot[0]["required"] = "false"
        with self.assertRaisesMessage(
            ValueError, "Custom application question is malformed"
        ):
            validate_custom_question_snapshot(snapshot)

    def test_cross_organization_program_cannot_be_snapshotted(self):
        self.app.program = Program.objects.create(
            organization=self.other, name="Foreign"
        )
        with self.assertRaisesMessage(ValueError, "program does not belong"):
            build_custom_question_snapshot(self.app)

    def test_missing_program_and_duplicate_ids_are_rejected(self):
        snapshot = build_custom_question_snapshot(self.app)
        snapshot.append(snapshot[0].copy())
        with self.assertRaisesMessage(
            ValueError, "Custom application question is malformed"
        ):
            validate_custom_question_snapshot(snapshot)
        self.app.program = None
        with self.assertRaisesMessage(ValueError, "program is required"):
            build_custom_question_snapshot(self.app)
