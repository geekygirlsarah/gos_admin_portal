from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from applications.models import (
    Application,
    ApplicationDocumentSubmission,
    ApplicationFixedAnswer,
    ApplicationGuardianDraft,
    ApplicationIdentityProposal,
    ApplicationQuestionAnswer,
    ApplicationQuestionSet,
    ApplicationRelationshipProposal,
    ApplicationStudentDraft,
    StudentApplicationAnswer,
)
from applications.proposals import ApplicationFinalizationError
from audit.events import AuditEvent
from audit.models import AuditLog
from organizations.models import Organization, UserOrganizationMembership
from programs.models import (
    Adult,
    AdultStudentRelationship,
    Enrollment,
    Program,
    ProgramDocument,
    Student,
)


class ApplicationProposalReviewTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(
            name="Proposal Review Organization",
            slug="proposal-review",
            subdomain="proposal-review",
        )
        self.program = Program.objects.create(
            organization=self.organization,
            name="Proposal Review Program",
            active=True,
        )
        reviewer = get_user_model().objects.create_user(
            username="proposal-reviewer",
            email="reviewer@example.test",
            password=None,
        )
        permission, _ = Permission.objects.get_or_create(
            content_type=ContentType.objects.get_for_model(Application),
            codename="review_application",
            defaults={"name": "Can review applications"},
        )
        group, _ = Group.objects.get_or_create(name="LeadMentor")
        group.permissions.add(permission)
        reviewer.groups.add(group)
        UserOrganizationMembership.objects.create(
            user=reviewer,
            organization=self.organization,
            role="LeadMentor",
            is_active=True,
        )
        self.reviewer = reviewer
        self.client = Client(HTTP_HOST=f"{self.organization.subdomain}.localhost")
        self.client.force_login(reviewer)
        self.application = self.make_application()

    def make_application(self, **overrides):
        defaults = {
            "organization": self.organization,
            "program": self.program,
            "applicant_type": Application.Type.PARENT,
            "email": "guardian@example.test",
            "email_verified_at": timezone.now(),
            "status": Application.Status.SUBMITTED,
            "submitted_at": timezone.now(),
            "profile_workflow_version": 1,
        }
        defaults.update(overrides)
        return Application.objects.create(**defaults)

    def profile(self, model, email, first_name):
        values = {
            "organization": self.organization,
            "personal_email": email,
            "legal_first_name": first_name,
            "last_name": "Applicant",
            "is_incomplete_application_profile": True,
        }
        if model is Adult:
            values["is_parent"] = True
        return model.all_objects.create(**values)

    def add_identity_decision(
        self, slot, decision=ApplicationIdentityProposal.Decision.NO_MATCH
    ):
        return ApplicationIdentityProposal.objects.create(
            organization=self.organization,
            application=self.application,
            subject_slot=slot,
            match_state=ApplicationIdentityProposal.MatchState.NO_MATCH,
            decision=decision,
            reviewed_by=(
                self.reviewer
                if decision != ApplicationIdentityProposal.Decision.PENDING
                else None
            ),
            reviewed_at=(
                timezone.now()
                if decision != ApplicationIdentityProposal.Decision.PENDING
                else None
            ),
        )

    def add_new_profile_workflow(
        self, include_required_document=True, required_form=True, existing_student=False
    ):
        student = self.profile(Student, "student@example.test", "Student")
        if existing_student:
            student.is_incomplete_application_profile = False
            student.save(update_fields=["is_incomplete_application_profile"])
        guardian = self.profile(Adult, "guardian@example.test", "Guardian")
        self.application.student_profile = student
        self.application.primary_guardian_profile = guardian
        self.application.save(
            update_fields=["student_profile", "primary_guardian_profile", "updated_at"]
        )
        student_draft = ApplicationStudentDraft.objects.create(
            organization=self.organization,
            application=self.application,
            legal_first_name="Student",
            last_name="Applicant",
        )
        guardian_draft = ApplicationGuardianDraft.objects.create(
            organization=self.organization,
            application=self.application,
            slot=ApplicationGuardianDraft.Slot.PRIMARY,
            presence=ApplicationGuardianDraft.Presence.PRESENT,
            email="guardian@example.test",
            relationship_to_student="parent",
        )
        self.add_identity_decision(ApplicationIdentityProposal.SubjectSlot.STUDENT)
        self.add_identity_decision(
            ApplicationIdentityProposal.SubjectSlot.PRIMARY_GUARDIAN
        )
        ApplicationRelationshipProposal.objects.create(
            organization=self.organization,
            application=self.application,
            action=ApplicationRelationshipProposal.Action.ADD,
            student_draft=student_draft,
            guardian_draft=guardian_draft,
            relationship_to_student="parent",
            decision=ApplicationRelationshipProposal.Decision.APPROVED,
            reviewed_by=self.reviewer,
            reviewed_at=timezone.now(),
        )
        ApplicationRelationshipProposal.objects.create(
            organization=self.organization,
            application=self.application,
            action=ApplicationRelationshipProposal.Action.SET_PRIMARY,
            student_draft=student_draft,
            guardian_draft=guardian_draft,
            decision=ApplicationRelationshipProposal.Decision.APPROVED,
            reviewed_by=self.reviewer,
            reviewed_at=timezone.now(),
        )
        for proposal in ApplicationRelationshipProposal.objects.filter(
            application=self.application
        ):
            self.assertTrue(proposal.is_baseline_current(), str(proposal.action))
        document = ProgramDocument.objects.create(
            program=self.program,
            name="Permission form",
            is_required=required_form,
            is_active=True,
        )
        if include_required_document:
            ApplicationDocumentSubmission.objects.create(
                organization=self.organization,
                application=self.application,
                document=document,
                file="private/application_documents/signed.pdf",
            )
        ApplicationFixedAnswer.objects.create(
            organization=self.organization,
            application=self.application,
            source_step="step6-experience",
            question_key="interest_reason",
            answer="I enjoy building robots.",
        )
        question_set = ApplicationQuestionSet.objects.create(
            organization=self.organization,
            application=self.application,
            program=self.program,
        )
        ApplicationQuestionAnswer.objects.create(
            organization=self.organization,
            question_set=question_set,
            position=0,
            question_id=uuid4(),
            question_text="What will you build?",
            is_required=True,
            answer="A small rover.",
        )
        return student, guardian

    def test_identity_decision_confirms_only_tenant_candidate(self):
        student = Student.objects.create(
            organization=self.organization,
            legal_first_name="Returning",
            last_name="Student",
            personal_email="student@example.test",
        )
        ApplicationStudentDraft.objects.create(
            organization=self.organization,
            application=self.application,
            personal_email="student@example.test",
        )
        proposal = ApplicationIdentityProposal.objects.create(
            organization=self.organization,
            application=self.application,
            subject_slot=ApplicationIdentityProposal.SubjectSlot.STUDENT,
            match_state=ApplicationIdentityProposal.MatchState.UNIQUE,
            candidate_student=student,
        )

        response = self.client.post(
            reverse(
                "application_review_identity_decision",
                args=[self.application.application_id, proposal.pk],
            ),
            {
                "decision": ApplicationIdentityProposal.Decision.CONFIRMED,
                "target_profile": f"student:{student.pk}",
            },
        )

        self.assertRedirects(
            response,
            reverse(
                "application_review_detail", args=[self.application.application_id]
            ),
        )
        proposal.refresh_from_db()
        self.assertEqual(
            proposal.decision, ApplicationIdentityProposal.Decision.CONFIRMED
        )
        self.assertEqual(proposal.target_student_id, student.pk)
        self.assertIsNone(self.application.student_profile_id)
        self.assertFalse(AdultStudentRelationship.objects.exists())
        audit = AuditLog.objects.get(
            organization=self.organization,
            actor=self.reviewer,
            event=AuditEvent.ADMISSION_DECISION,
            resource_id=str(self.application.pk),
            after__identity_proposal_id=proposal.pk,
        )
        self.assertEqual(audit.before["decision"], "pending")
        self.assertEqual(audit.after["decision"], "confirmed")
        self.assertNotIn("student@example.test", str(audit.after))

    def test_identity_decision_rejects_foreign_target_and_unverified_reviewer(self):
        proposal = ApplicationIdentityProposal.objects.create(
            organization=self.organization,
            application=self.application,
            subject_slot=ApplicationIdentityProposal.SubjectSlot.STUDENT,
            match_state=ApplicationIdentityProposal.MatchState.AMBIGUOUS,
        )
        other_organization = Organization.objects.create(
            name="Foreign Proposal Review",
            slug="foreign-proposal-review",
            subdomain="foreign-proposal-review",
        )
        foreign_student = Student.objects.create(
            organization=other_organization,
            legal_first_name="Foreign",
            last_name="Student",
        )
        response = self.client.post(
            reverse(
                "application_review_identity_decision",
                args=[self.application.application_id, proposal.pk],
            ),
            {
                "decision": ApplicationIdentityProposal.Decision.CONFIRMED,
                "target_profile": f"student:{foreign_student.pk}",
            },
        )

        self.assertEqual(
            response.status_code,
            302,
            "A foreign identity target must be rejected and return to the review page.",
        )
        self.assertEqual(
            response["Location"],
            reverse("apply_start"),
        )
        proposal.refresh_from_db()
        self.assertEqual(
            proposal.decision, ApplicationIdentityProposal.Decision.PENDING
        )

    def test_relationship_decision_does_not_change_live_access(self):
        student = Student.objects.create(
            organization=self.organization,
            legal_first_name="Existing",
            last_name="Student",
        )
        guardian = Adult.objects.create(
            organization=self.organization,
            legal_first_name="Existing",
            last_name="Guardian",
            is_parent=True,
        )
        proposal = ApplicationRelationshipProposal.objects.create(
            organization=self.organization,
            application=self.application,
            action=ApplicationRelationshipProposal.Action.ADD,
            student_profile=student,
            guardian_profile=guardian,
            relationship_to_student="parent",
        )

        response = self.client.post(
            reverse(
                "application_review_relationship_decision",
                args=[self.application.application_id, proposal.pk],
            ),
            {"decision": ApplicationRelationshipProposal.Decision.APPROVED},
        )

        self.assertEqual(response.status_code, 302)
        proposal.refresh_from_db()
        self.application.refresh_from_db()
        self.assertEqual(
            proposal.decision, ApplicationRelationshipProposal.Decision.APPROVED
        )
        self.assertEqual(self.application.status, Application.Status.SUBMITTED)
        self.assertFalse(
            AdultStudentRelationship.objects.filter(
                student=student, adult=guardian
            ).exists()
        )

    def test_non_reviewer_cannot_decide_a_relationship_proposal(self):
        student = Student.objects.create(
            organization=self.organization,
            legal_first_name="Existing",
            last_name="Student",
        )
        guardian = Adult.objects.create(
            organization=self.organization,
            legal_first_name="Existing",
            last_name="Guardian",
            is_parent=True,
        )
        proposal = ApplicationRelationshipProposal.objects.create(
            organization=self.organization,
            application=self.application,
            action=ApplicationRelationshipProposal.Action.ADD,
            student_profile=student,
            guardian_profile=guardian,
            relationship_to_student="parent",
        )
        unauthorized = get_user_model().objects.create_user(
            username="unauthorized-reviewer",
            email="unauthorized@example.test",
            password=None,
        )
        client = Client(HTTP_HOST=f"{self.organization.subdomain}.localhost")
        client.force_login(unauthorized)

        response = client.post(
            reverse(
                "application_review_relationship_decision",
                args=[self.application.application_id, proposal.pk],
            ),
            {"decision": ApplicationRelationshipProposal.Decision.APPROVED},
        )

        self.assertEqual(response.status_code, 403)
        proposal.refresh_from_db()
        self.assertEqual(
            proposal.decision, ApplicationRelationshipProposal.Decision.PENDING
        )
        self.assertFalse(
            AdultStudentRelationship.objects.filter(
                student=student, adult=guardian
            ).exists()
        )

    def test_profile_workflow_cannot_use_legacy_conversion_endpoint(self):
        self.application.status = Application.Status.APPROVED_SIGNED
        self.application.save(update_fields=["status", "updated_at"])

        response = self.client.post(
            reverse(
                "application_review_convert", args=[self.application.application_id]
            )
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            reverse(
                "application_review_detail", args=[self.application.application_id]
            ),
        )
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, Application.Status.APPROVED_SIGNED)
        self.assertIsNone(self.application.converted_student_id)

    def test_profile_workflow_cannot_use_legacy_json_edit_form(self):
        student, _ = self.add_new_profile_workflow(required_form=False)
        edit_url = reverse(
            "application_review_edit", args=[self.application.application_id]
        )

        response = self.client.get(edit_url)
        self.assertRedirects(
            response,
            reverse(
                "application_review_detail", args=[self.application.application_id]
            ),
        )
        response = self.client.post(edit_url, {"email": "changed@example.test"})
        self.assertRedirects(
            response,
            reverse(
                "application_review_detail", args=[self.application.application_id]
            ),
        )
        self.application.refresh_from_db()
        student.refresh_from_db()
        self.assertEqual(self.application.email, "guardian@example.test")
        self.assertEqual(student.legal_first_name, "Student")

    def test_stale_relationship_decision_is_rejected(self):
        student = Student.objects.create(
            organization=self.organization,
            legal_first_name="Existing",
            last_name="Student",
        )
        guardian = Adult.objects.create(
            organization=self.organization,
            legal_first_name="Existing",
            last_name="Guardian",
            is_parent=True,
        )
        proposal = ApplicationRelationshipProposal.objects.create(
            organization=self.organization,
            application=self.application,
            action=ApplicationRelationshipProposal.Action.ADD,
            student_profile=student,
            guardian_profile=guardian,
            relationship_to_student="parent",
        )
        student.last_name = "Changed after proposal"
        student.save(update_fields=["last_name"])

        response = self.client.post(
            reverse(
                "application_review_relationship_decision",
                args=[self.application.application_id, proposal.pk],
            ),
            {"decision": ApplicationRelationshipProposal.Decision.APPROVED},
        )

        self.assertEqual(response.status_code, 302)
        proposal.refresh_from_db()
        self.assertEqual(
            proposal.decision, ApplicationRelationshipProposal.Decision.PENDING
        )

    def test_unsubmitted_profile_application_cannot_be_approved(self):
        self.application.status = Application.Status.DRAFT
        self.application.submitted_at = None
        self.application.save(update_fields=["status", "submitted_at", "updated_at"])
        student, guardian = self.add_new_profile_workflow(required_form=False)

        detail = self.client.get(
            reverse("application_review_detail", args=[self.application.application_id])
        )
        self.assertFalse(detail.context["can_approve"])
        with patch(
            "applications.views.review.send_application_approved_email"
        ) as notice:
            self.client.post(
                reverse(
                    "application_review_approve", args=[self.application.application_id]
                )
            )

        self.application.refresh_from_db()
        student.refresh_from_db()
        guardian.refresh_from_db()
        self.assertEqual(self.application.status, Application.Status.DRAFT)
        self.assertTrue(student.is_incomplete_application_profile)
        self.assertTrue(guardian.is_incomplete_application_profile)
        self.assertFalse(Enrollment.all_objects.filter(student=student).exists())
        self.assertFalse(
            AdultStudentRelationship.all_objects.filter(student=student).exists()
        )
        notice.assert_not_called()

    def test_no_match_identity_cannot_select_an_existing_student(self):
        student, guardian = self.add_new_profile_workflow(
            required_form=False, existing_student=True
        )

        with patch(
            "applications.views.review.send_application_approved_email"
        ) as notice:
            self.client.post(
                reverse(
                    "application_review_approve", args=[self.application.application_id]
                )
            )

        self.application.refresh_from_db()
        guardian.refresh_from_db()
        self.assertEqual(self.application.status, Application.Status.SUBMITTED)
        self.assertTrue(guardian.is_incomplete_application_profile)
        self.assertFalse(Enrollment.all_objects.filter(student=student).exists())
        self.assertFalse(
            AdultStudentRelationship.all_objects.filter(student=student).exists()
        )
        notice.assert_not_called()

    def test_approval_waits_for_resolved_proposals(self):
        student, _ = self.add_new_profile_workflow(
            include_required_document=False,
            required_form=False,
        )
        pending = ApplicationIdentityProposal.objects.create(
            organization=self.organization,
            application=self.application,
            subject_slot=ApplicationIdentityProposal.SubjectSlot.SECONDARY_GUARDIAN,
            match_state=ApplicationIdentityProposal.MatchState.NO_MATCH,
        )

        response = self.client.post(
            reverse(
                "application_review_approve", args=[self.application.application_id]
            )
        )

        self.assertEqual(response.status_code, 302)
        self.application.refresh_from_db()
        student.refresh_from_db()
        self.assertEqual(self.application.status, Application.Status.SUBMITTED)
        self.assertTrue(student.is_incomplete_application_profile)
        self.assertFalse(Enrollment.all_objects.filter(student=student).exists())
        pending.refresh_from_db()
        self.assertEqual(pending.decision, ApplicationIdentityProposal.Decision.PENDING)

    def test_approval_waits_for_required_documents_even_when_proposals_are_resolved(
        self,
    ):
        student, _ = self.add_new_profile_workflow(include_required_document=False)

        response = self.client.post(
            reverse(
                "application_review_approve", args=[self.application.application_id]
            )
        )

        self.assertEqual(response.status_code, 302)
        self.application.refresh_from_db()
        student.refresh_from_db()
        self.assertEqual(self.application.status, Application.Status.SUBMITTED)
        self.assertTrue(student.is_incomplete_application_profile)
        self.assertFalse(Enrollment.all_objects.filter(student=student).exists())

    def test_final_approval_activates_profiles_and_relationships_atomically(self):
        student, guardian = self.add_new_profile_workflow()

        with patch("applications.views.review.send_application_approved_email"):
            response = self.client.post(
                reverse(
                    "application_review_approve", args=[self.application.application_id]
                ),
                follow=True,
            )

        response_messages = "; ".join(
            str(message) for message in response.context["messages"]
        )
        self.assertEqual(response.status_code, 200, response_messages)
        self.application.refresh_from_db()
        student.refresh_from_db()
        guardian.refresh_from_db()
        self.assertEqual(
            self.application.status,
            Application.Status.APPROVED_SIGNED,
            response_messages,
        )
        self.assertFalse(student.is_incomplete_application_profile)
        self.assertFalse(guardian.is_incomplete_application_profile)
        self.assertTrue(
            AdultStudentRelationship.objects.filter(
                student=student, adult=guardian
            ).exists()
        )
        student.refresh_from_db()
        self.assertEqual(student.primary_contact, guardian)
        self.assertTrue(
            Enrollment.objects.filter(
                organization=self.organization,
                student=student,
                program=self.program,
                active=True,
            ).exists()
        )
        answers = {
            answer.question_key: answer
            for answer in StudentApplicationAnswer.objects.filter(
                application=self.application,
                student=student,
            )
        }
        self.assertEqual(answers["interest_reason"].answer, "I enjoy building robots.")
        custom_answer = next(
            answer
            for answer in answers.values()
            if answer.source_step == "step6-custom"
        )
        self.assertEqual(custom_answer.question_text, "What will you build?")
        self.assertEqual(custom_answer.answer, "A small rover.")

    def test_late_finalization_conflict_rolls_back_all_profile_and_relationship_changes(
        self,
    ):
        student, guardian = self.add_new_profile_workflow()

        with patch(
            "applications.proposals._apply_relationship_proposals",
            side_effect=ApplicationFinalizationError("simulated stale relationship"),
        ):
            response = self.client.post(
                reverse(
                    "application_review_approve", args=[self.application.application_id]
                ),
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        self.application.refresh_from_db()
        student.refresh_from_db()
        guardian.refresh_from_db()
        self.assertEqual(self.application.status, Application.Status.SUBMITTED)
        self.assertTrue(student.is_incomplete_application_profile)
        self.assertTrue(guardian.is_incomplete_application_profile)
        self.assertFalse(
            AdultStudentRelationship.objects.filter(
                student=student, adult=guardian
            ).exists()
        )
        self.assertFalse(Enrollment.all_objects.filter(student=student).exists())
        self.assertEqual(student.profile_revision, 1)

    def test_final_approval_applies_reviewed_replacement_removal_and_primary_change(
        self,
    ):
        student = Student.objects.create(
            organization=self.organization,
            legal_first_name="Existing",
            last_name="Student",
            personal_email="student@example.test",
        )
        old_primary = Adult.objects.create(
            organization=self.organization,
            legal_first_name="Old",
            last_name="Guardian",
            personal_email="old-guardian@example.test",
            is_parent=True,
        )
        secondary = Adult.objects.create(
            organization=self.organization,
            legal_first_name="Secondary",
            last_name="Guardian",
            personal_email="secondary-guardian@example.test",
            is_parent=True,
        )
        new_primary = Adult.objects.create(
            organization=self.organization,
            legal_first_name="New",
            last_name="Guardian",
            personal_email="new-guardian@example.test",
            is_parent=True,
        )
        old_relationship = AdultStudentRelationship.objects.create(
            organization=self.organization,
            adult=old_primary,
            student=student,
            relationship_to_student="parent",
        )
        secondary_relationship = AdultStudentRelationship.objects.create(
            organization=self.organization,
            adult=secondary,
            student=student,
            relationship_to_student="parent",
        )
        student.primary_contact_relationship = old_relationship
        student.save(update_fields=["primary_contact_relationship"])
        self.application.email = "new-guardian@example.test"
        self.application.student_profile = student
        self.application.primary_guardian_profile = old_primary
        self.application.secondary_guardian_profile = secondary
        self.application.save(
            update_fields=[
                "email",
                "student_profile",
                "primary_guardian_profile",
                "secondary_guardian_profile",
                "updated_at",
            ]
        )
        ApplicationStudentDraft.objects.create(
            organization=self.organization,
            application=self.application,
            personal_email="student@example.test",
        )
        ApplicationGuardianDraft.objects.create(
            organization=self.organization,
            application=self.application,
            slot=ApplicationGuardianDraft.Slot.PRIMARY,
            presence=ApplicationGuardianDraft.Presence.PRESENT,
            email="new-guardian@example.test",
            relationship_to_student="parent",
        )
        identity_decisions = (
            (
                ApplicationIdentityProposal.SubjectSlot.STUDENT,
                ApplicationIdentityProposal.MatchState.UNIQUE,
                {"candidate_student": student, "target_student": student},
            ),
            (
                ApplicationIdentityProposal.SubjectSlot.PRIMARY_GUARDIAN,
                ApplicationIdentityProposal.MatchState.UNIQUE,
                {"candidate_adult": new_primary, "target_adult": new_primary},
            ),
            (
                ApplicationIdentityProposal.SubjectSlot.SECONDARY_GUARDIAN,
                ApplicationIdentityProposal.MatchState.NO_MATCH,
                {},
            ),
        )
        for slot, match_state, profiles in identity_decisions:
            ApplicationIdentityProposal.objects.create(
                organization=self.organization,
                application=self.application,
                subject_slot=slot,
                match_state=match_state,
                decision=(
                    ApplicationIdentityProposal.Decision.CONFIRMED
                    if profiles
                    else ApplicationIdentityProposal.Decision.NO_MATCH
                ),
                reviewed_by=self.reviewer,
                reviewed_at=timezone.now(),
                **profiles,
            )
        proposals = (
            (
                ApplicationRelationshipProposal.Action.REPLACE,
                old_relationship,
                new_primary,
            ),
            (
                ApplicationRelationshipProposal.Action.REMOVE,
                secondary_relationship,
                secondary,
            ),
        )
        for action, relationship, new_guardian in proposals:
            ApplicationRelationshipProposal.objects.create(
                organization=self.organization,
                application=self.application,
                action=action,
                student_profile=student,
                guardian_profile=new_guardian,
                relationship_to_student="parent",
                baseline_relationship_id=relationship.pk,
                baseline_relationship_revision=relationship.revision,
                decision=ApplicationRelationshipProposal.Decision.APPROVED,
                reviewed_by=self.reviewer,
                reviewed_at=timezone.now(),
            )
        ApplicationRelationshipProposal.objects.create(
            organization=self.organization,
            application=self.application,
            action=ApplicationRelationshipProposal.Action.SET_PRIMARY,
            student_profile=student,
            guardian_profile=new_primary,
            baseline_primary_relationship_id=old_relationship.pk,
            baseline_primary_relationship_revision=old_relationship.revision,
            decision=ApplicationRelationshipProposal.Decision.APPROVED,
            reviewed_by=self.reviewer,
            reviewed_at=timezone.now(),
        )

        with patch("applications.views.review.send_application_approved_email"):
            response = self.client.post(
                reverse(
                    "application_review_approve", args=[self.application.application_id]
                ),
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        self.application.refresh_from_db()
        student.refresh_from_db()
        self.assertEqual(self.application.status, Application.Status.APPROVED)
        self.assertEqual(self.application.primary_guardian_profile_id, new_primary.pk)
        self.assertIsNone(self.application.secondary_guardian_profile_id)
        self.assertTrue(
            AdultStudentRelationship.objects.filter(
                student=student,
                adult=new_primary,
            ).exists()
        )
        self.assertFalse(
            AdultStudentRelationship.objects.filter(
                student=student,
                adult=old_primary,
            ).exists()
        )
        self.assertFalse(
            AdultStudentRelationship.objects.filter(
                student=student,
                adult=secondary,
            ).exists()
        )
        self.assertEqual(student.primary_contact, new_primary)
