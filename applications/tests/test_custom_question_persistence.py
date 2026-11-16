from django.test import TestCase

from applications.answers import freeze_custom_question_snapshot
from applications.models import Application, ProgramApplicationQuestion
from organizations.models import Organization
from programs.models import Program


class CustomQuestionPersistenceTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            name="Questions", slug="question-persist", subdomain="question-persist"
        )
        self.other = Organization.objects.create(
            name="Other",
            slug="question-persist-other",
            subdomain="question-persist-other",
        )
        self.program = Program.objects.create(organization=self.org, name="Program")
        self.question = ProgramApplicationQuestion.objects.create(
            organization=self.org,
            program=self.program,
            text="Original?",
            is_required=True,
        )
        self.app = Application.objects.create(
            organization=self.org,
            program=self.program,
            applicant_type=Application.Type.PARENT,
            data={"step5-student": {"legal_first_name": "Example"}},
        )

    def test_persists_once_and_keeps_unrelated_fresh_data(self):
        stale = Application.objects.get(pk=self.app.pk)
        self.app.data["step7-primaryparent"] = {"email_updates": True}
        self.app.save(update_fields=["data"])
        snapshot = freeze_custom_question_snapshot(stale)
        self.app.refresh_from_db()
        self.assertEqual(self.app.data["step6-custom"], snapshot)
        self.assertEqual(self.app.data["_custom_question_program_id"], self.program.pk)
        self.assertTrue(self.app.data["step7-primaryparent"]["email_updates"])
        self.question.text = "Changed?"
        self.question.save()
        self.assertEqual(freeze_custom_question_snapshot(stale), snapshot)
        self.assertEqual(snapshot[0]["question_text"], "Original?")

    def test_empty_snapshot_is_still_frozen(self):
        self.question.is_active = False
        self.question.save()
        self.assertEqual(freeze_custom_question_snapshot(self.app), [])
        self.question.is_active = True
        self.question.save()
        self.assertEqual(freeze_custom_question_snapshot(self.app), [])

    def test_stale_program_and_mismatched_snapshot_are_rejected(self):
        other_program = Program.objects.create(organization=self.org, name="Other")
        stale = Application.objects.get(pk=self.app.pk)
        self.app.program = other_program
        self.app.save(update_fields=["program"])
        with self.assertRaisesMessage(ValueError, "program has changed"):
            freeze_custom_question_snapshot(stale)
        self.app.data = {
            "step6-custom": [],
            "_custom_question_program_id": self.program.pk,
        }
        self.app.save(update_fields=["data"])
        with self.assertRaisesMessage(ValueError, "different program"):
            freeze_custom_question_snapshot(self.app)
        self.app.refresh_from_db()
        self.assertEqual(self.app.data["_custom_question_program_id"], self.program.pk)

    def test_unbound_or_malformed_snapshot_is_not_overwritten(self):
        for data in (
            {"step6-custom": []},
            {"_custom_question_program_id": self.program.pk},
            {"step6-custom": [], "_custom_question_program_id": True},
            {"step6-custom": "bad", "_custom_question_program_id": self.program.pk},
            ["bad data"],
        ):
            with self.subTest(data=data):
                self.app.data = data
                self.app.save(update_fields=["data"])
                with self.assertRaises(ValueError):
                    freeze_custom_question_snapshot(self.app)
                self.app.refresh_from_db()
                self.assertEqual(self.app.data, data)

    def test_missing_foreign_or_changed_organization_and_completed_are_rejected(self):
        self.app.program = None
        self.app.save(update_fields=["program"])
        with self.assertRaisesMessage(ValueError, "program is required"):
            freeze_custom_question_snapshot(self.app)
        self.app.program = Program.objects.create(
            organization=self.other, name="Foreign"
        )
        self.app.save(update_fields=["program"])
        with self.assertRaisesMessage(ValueError, "does not belong"):
            freeze_custom_question_snapshot(self.app)
        self.app.program = self.program
        self.app.status = Application.Status.SUBMITTED
        self.app.save(update_fields=["program", "status"])
        with self.assertRaisesMessage(ValueError, "no longer editable"):
            freeze_custom_question_snapshot(self.app)
        stale = Application.objects.get(pk=self.app.pk)
        Application.objects.filter(pk=self.app.pk).update(organization=self.other)
        with self.assertRaisesMessage(ValueError, "organization has changed"):
            freeze_custom_question_snapshot(stale)
