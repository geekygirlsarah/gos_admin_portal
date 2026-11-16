from unittest.mock import patch

from django.test import RequestFactory, TestCase

from applications.answers import freeze_custom_question_snapshot
from applications.models import Application, ProgramApplicationQuestion
from applications.views.steps_mentor import MentorClearanceInterestView
from applications.views.utils import _save_step_data
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

    def test_persisted_normalization_uses_fresh_data(self):
        self.app.data = {"step5": {"legal_first_name": "Example"}}
        self.app.save(update_fields=["data"])
        stale = Application.objects.get(pk=self.app.pk)
        snapshot = freeze_custom_question_snapshot(self.app)

        normalized = stale.normalize_step_data(save=True)

        self.app.refresh_from_db()
        self.assertEqual(normalized, self.app.data)
        self.assertEqual(normalized["step6-custom"], snapshot)
        self.assertEqual(normalized["_custom_question_program_id"], self.program.pk)
        self.assertEqual(normalized["step5-student"]["legal_first_name"], "Example")
        self.assertNotIn("step5", normalized)

    def test_persisted_normalization_refreshes_even_without_stale_legacy_keys(self):
        stale = Application.objects.get(pk=self.app.pk)
        snapshot = freeze_custom_question_snapshot(self.app)

        normalized = stale.normalize_step_data(save=True)

        self.assertEqual(normalized["step6-custom"], snapshot)
        self.assertEqual(stale.data, normalized)

    def test_persisted_normalization_does_not_follow_changed_organization(self):
        stale = Application.objects.get(pk=self.app.pk)
        Application.objects.filter(pk=self.app.pk).update(organization=self.other)

        with self.assertRaises(Application.DoesNotExist):
            stale.normalize_step_data(save=True)

    def test_stale_step_save_preserves_snapshot_other_steps_and_progress(self):
        stale = Application.objects.get(pk=self.app.pk)
        snapshot = freeze_custom_question_snapshot(self.app)
        Application.objects.filter(pk=self.app.pk).update(current_step=9)
        _save_step_data(self.app, "step7-primaryparent", {"email_updates": True}, 8)

        _save_step_data(stale, "step6-experience", {"referral_source": "Friend"}, 7)

        self.app.refresh_from_db()
        self.assertEqual(self.app.data["step6-custom"], snapshot)
        self.assertEqual(self.app.data["_custom_question_program_id"], self.program.pk)
        self.assertTrue(self.app.data["step7-primaryparent"]["email_updates"])
        self.assertEqual(self.app.data["step6-experience"]["referral_source"], "Friend")
        self.assertEqual(self.app.current_step, 9)
        self.assertEqual(stale.data, self.app.data)
        self.assertEqual(stale.current_step, 9)

    def test_step_save_does_not_follow_changed_organization(self):
        stale = Application.objects.get(pk=self.app.pk)
        Application.objects.filter(pk=self.app.pk).update(organization=self.other)

        with self.assertRaises(Application.DoesNotExist):
            _save_step_data(stale, "step6-experience", {}, 7)

    def test_mentor_clearance_cleanup_preserves_intervening_data(self):
        self.app.applicant_type = Application.Type.MENTOR
        self.app.data = {"mentor_clearance_detail": {"previous": "detail"}}
        self.app.save(update_fields=["applicant_type", "data"])
        request = RequestFactory().post("/", {"interested": "no"})
        request.organization = self.org

        def save_then_update(*args, **kwargs):
            _save_step_data(*args, **kwargs)
            current = Application.objects.get(pk=self.app.pk)
            current.data["other_step"] = {"answer": "newer"}
            current.save(update_fields=["data"])

        with (
            patch(
                "applications.views.steps_mentor._get_application_or_404",
                return_value=self.app,
            ),
            patch(
                "applications.views.steps_mentor._require_mentor_verified",
                return_value=None,
            ),
            patch(
                "applications.views.utils._save_step_data", side_effect=save_then_update
            ),
        ):
            response = MentorClearanceInterestView.as_view()(
                request, app_id=self.app.application_id
            )

        self.assertEqual(response.status_code, 302)
        self.app.refresh_from_db()
        self.assertEqual(self.app.data["other_step"], {"answer": "newer"})
        self.assertEqual(self.app.data["mentor_clearance_interest"]["interested"], "no")
        self.assertNotIn("mentor_clearance_detail", self.app.data)

    def test_interested_mentor_keeps_clearance_detail(self):
        self.app.data = {"mentor_clearance_detail": {"previous": "detail"}}
        self.app.save(update_fields=["data"])
        request = RequestFactory().post("/", {"interested": "yes"})
        request.organization = self.org

        with (
            patch(
                "applications.views.steps_mentor._get_application_or_404",
                return_value=self.app,
            ),
            patch(
                "applications.views.steps_mentor._require_mentor_verified",
                return_value=None,
            ),
        ):
            response = MentorClearanceInterestView.as_view()(
                request, app_id=self.app.application_id
            )

        self.assertEqual(response.status_code, 302)
        self.app.refresh_from_db()
        self.assertEqual(
            self.app.data["mentor_clearance_detail"], {"previous": "detail"}
        )
        self.assertEqual(
            self.app.data["mentor_clearance_interest"]["interested"], "yes"
        )

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
