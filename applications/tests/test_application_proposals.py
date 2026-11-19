from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from applications.identity import (
    ApplicationProfileEditAuthorization,
    evaluate_application_profile_edit,
)
from applications.models import (
    Application,
    ApplicationGuardianDraft,
    ApplicationIdentityProposal,
    ApplicationRelationshipProposal,
    ApplicationStudentDraft,
)
from applications.proposals import validate_application_profile_email_claims
from applications.services import (
    document_recipient,
    resolve_application_person_identity,
)
from applications.tests.access_helpers import verify_application_browser
from organizations.models import Organization
from programs.models import Adult, AdultStudentRelationship, Student


class ApplicationProposalModelTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(
            name="Proposal Organization",
            slug="proposal-org",
            subdomain="proposal-org",
        )
        self.other_organization = Organization.objects.create(
            name="Other Proposal Organization",
            slug="other-proposal-org",
            subdomain="other-proposal-org",
        )
        self.application = Application.objects.create(
            organization=self.organization,
            email="applicant@example.test",
            applicant_type=Application.Type.PARENT,
        )
        self.student = Student.objects.create(
            organization=self.organization,
            legal_first_name="Sam",
            last_name="Student",
            personal_email="sam@example.test",
        )
        self.guardian = Adult.objects.create(
            organization=self.organization,
            legal_first_name="Pat",
            last_name="Guardian",
            personal_email="pat@example.test",
            is_parent=True,
        )

    def test_identity_resolution_distinguishes_no_match_unique_and_ambiguous(self):
        no_match = resolve_application_person_identity(
            "unknown@example.test", self.organization
        )
        unique = resolve_application_person_identity(
            " SAM@example.test ", self.organization
        )
        Adult.objects.create(
            organization=self.organization,
            legal_first_name="Other",
            last_name="Guardian",
            personal_email="sam@example.test",
            is_parent=True,
        )
        ambiguous = resolve_application_person_identity(
            "sam@example.test", self.organization
        )
        Adult.objects.create(
            organization=self.other_organization,
            legal_first_name="Foreign",
            last_name="Guardian",
            personal_email="pat@example.test",
            is_parent=True,
        )
        tenant_scoped = resolve_application_person_identity(
            "pat@example.test", self.organization
        )

        self.assertEqual(
            no_match.status, ApplicationIdentityProposal.MatchState.NO_MATCH
        )
        self.assertIsNone(no_match.profile)
        self.assertEqual(unique.status, ApplicationIdentityProposal.MatchState.UNIQUE)
        self.assertEqual(unique.profile, self.student)
        self.assertEqual(
            ambiguous.status, ApplicationIdentityProposal.MatchState.AMBIGUOUS
        )
        self.assertIsNone(ambiguous.profile)
        self.assertEqual(
            tenant_scoped.status, ApplicationIdentityProposal.MatchState.UNIQUE
        )
        self.assertEqual(tenant_scoped.profile, self.guardian)

    def test_ambiguous_identity_proposal_stores_no_candidate(self):
        proposal = ApplicationIdentityProposal.objects.create(
            organization=self.organization,
            application=self.application,
            subject_slot=ApplicationIdentityProposal.SubjectSlot.PRIMARY_GUARDIAN,
            match_state=ApplicationIdentityProposal.MatchState.AMBIGUOUS,
        )

        self.assertIsNone(proposal.candidate_student_id)
        self.assertIsNone(proposal.candidate_adult_id)
        self.assertEqual(
            proposal.decision, ApplicationIdentityProposal.Decision.PENDING
        )

    def test_lead_can_set_a_target_for_ambiguous_identity_with_a_baseline(self):
        proposal = ApplicationIdentityProposal.objects.create(
            organization=self.organization,
            application=self.application,
            subject_slot=ApplicationIdentityProposal.SubjectSlot.STUDENT,
            match_state=ApplicationIdentityProposal.MatchState.AMBIGUOUS,
        )

        proposal.set_target_profile(self.student)
        proposal.save(
            update_fields=["target_student", "target_baseline_revision", "updated_at"]
        )

        self.assertEqual(proposal.target_student, self.student)
        self.assertEqual(
            proposal.target_baseline_revision, self.student.profile_revision
        )
        self.assertTrue(proposal.is_baseline_current())

    def test_identity_proposal_rejects_stale_profile_baseline(self):
        proposal = ApplicationIdentityProposal.objects.create(
            organization=self.organization,
            application=self.application,
            subject_slot=ApplicationIdentityProposal.SubjectSlot.STUDENT,
            match_state=ApplicationIdentityProposal.MatchState.UNIQUE,
            candidate_student=self.student,
        )
        self.assertTrue(proposal.is_baseline_current())

        self.student.last_name = "Updated Student"
        self.student.save(update_fields=["last_name"])

        self.assertFalse(proposal.is_baseline_current())

    def test_identity_proposal_rejects_stale_guardian_baseline(self):
        proposal = ApplicationIdentityProposal.objects.create(
            organization=self.organization,
            application=self.application,
            subject_slot=ApplicationIdentityProposal.SubjectSlot.PRIMARY_GUARDIAN,
            match_state=ApplicationIdentityProposal.MatchState.UNIQUE,
            candidate_adult=self.guardian,
        )
        self.assertTrue(proposal.is_baseline_current())

        self.guardian.last_name = "Updated Guardian"
        self.guardian.save(update_fields=["last_name"])

        self.assertFalse(proposal.is_baseline_current())

    def test_identity_proposal_with_deleted_candidate_is_not_current(self):
        proposal = ApplicationIdentityProposal.objects.create(
            organization=self.organization,
            application=self.application,
            subject_slot=ApplicationIdentityProposal.SubjectSlot.STUDENT,
            match_state=ApplicationIdentityProposal.MatchState.UNIQUE,
            candidate_student=self.student,
        )

        self.student.delete()
        proposal.refresh_from_db()

        self.assertIsNone(proposal.candidate_student_id)
        self.assertFalse(proposal.is_baseline_current())

    def test_identity_proposal_rejects_cross_organization_candidates(self):
        foreign_student = Student.objects.create(
            organization=self.other_organization,
            legal_first_name="Foreign",
            last_name="Student",
        )
        proposal = ApplicationIdentityProposal(
            organization=self.organization,
            application=self.application,
            subject_slot=ApplicationIdentityProposal.SubjectSlot.STUDENT,
            match_state=ApplicationIdentityProposal.MatchState.UNIQUE,
            candidate_student=foreign_student,
            candidate_baseline_revision=foreign_student.profile_revision,
        )

        with self.assertRaises(ValidationError):
            proposal.save()

    def test_relationship_proposal_is_tenant_bound_and_does_not_change_relationships(
        self,
    ):
        student_draft = ApplicationStudentDraft.objects.create(
            organization=self.organization,
            application=self.application,
        )
        guardian_draft = ApplicationGuardianDraft.objects.create(
            organization=self.organization,
            application=self.application,
            slot=ApplicationGuardianDraft.Slot.PRIMARY,
            presence=ApplicationGuardianDraft.Presence.PRESENT,
            relationship_to_student="parent",
        )
        relationship_count = AdultStudentRelationship.all_objects.count()
        proposal = ApplicationRelationshipProposal.objects.create(
            organization=self.organization,
            application=self.application,
            action=ApplicationRelationshipProposal.Action.ADD,
            student_draft=student_draft,
            guardian_draft=guardian_draft,
            relationship_to_student="parent",
            baseline_application_updated_at=self.application.updated_at,
        )

        self.assertTrue(proposal.is_baseline_current())
        self.assertEqual(
            AdultStudentRelationship.all_objects.count(), relationship_count
        )
        self.assertEqual(
            proposal.decision, ApplicationRelationshipProposal.Decision.PENDING
        )

    def test_relationship_proposal_rejects_stale_application_draft_revision(self):
        student_draft = ApplicationStudentDraft.objects.create(
            organization=self.organization,
            application=self.application,
        )
        guardian_draft = ApplicationGuardianDraft.objects.create(
            organization=self.organization,
            application=self.application,
            slot=ApplicationGuardianDraft.Slot.PRIMARY,
            presence=ApplicationGuardianDraft.Presence.PRESENT,
            relationship_to_student="parent",
        )
        proposal = ApplicationRelationshipProposal.objects.create(
            organization=self.organization,
            application=self.application,
            action=ApplicationRelationshipProposal.Action.ADD,
            student_draft=student_draft,
            guardian_draft=guardian_draft,
            relationship_to_student="parent",
        )
        self.assertTrue(proposal.is_baseline_current())

        ApplicationStudentDraft.objects.filter(pk=student_draft.pk).update(
            legal_first_name="Updated draft"
        )

        self.assertFalse(proposal.is_baseline_current())

    def test_relationship_proposal_rejects_cross_organization_draft(self):
        foreign_application = Application.objects.create(
            organization=self.other_organization,
        )
        foreign_guardian_draft = ApplicationGuardianDraft.objects.create(
            organization=self.other_organization,
            application=foreign_application,
            slot=ApplicationGuardianDraft.Slot.PRIMARY,
        )
        proposal = ApplicationRelationshipProposal(
            organization=self.organization,
            application=self.application,
            action=ApplicationRelationshipProposal.Action.ADD,
            student_profile=self.student,
            guardian_draft=foreign_guardian_draft,
            relationship_to_student="parent",
        )

        with self.assertRaises(ValidationError):
            proposal.save()

    def test_profile_workflow_document_recipient_comes_from_primary_guardian_profile(
        self,
    ):
        guardian = Adult.objects.create(
            organization=self.organization,
            legal_first_name="Document",
            last_name="Guardian",
            personal_email="guardian@example.test",
            is_parent=True,
        )
        self.application.primary_guardian_profile = guardian
        self.application.profile_workflow_version = 1
        self.application.save(
            update_fields=[
                "primary_guardian_profile",
                "profile_workflow_version",
                "updated_at",
            ]
        )

        self.assertEqual(document_recipient(self.application), "guardian@example.test")

    def test_email_conflicts_block_new_drafts_but_do_not_rewrite_historical_duplicates(
        self,
    ):
        draft_student = Student.all_objects.create(
            organization=self.organization,
            legal_first_name="Draft",
            last_name="Student",
            personal_email="shared@example.test",
            is_incomplete_application_profile=True,
        )
        draft_guardian = Adult.all_objects.create(
            organization=self.organization,
            legal_first_name="Draft",
            last_name="Guardian",
            personal_email="shared@example.test",
            is_parent=True,
            is_incomplete_application_profile=True,
        )

        self.assertTrue(
            validate_application_profile_email_claims(
                self.application, [draft_student, draft_guardian]
            )
        )

        existing_student = Student.all_objects.create(
            organization=self.organization,
            legal_first_name="Historical",
            last_name="Student",
            personal_email="historical-shared@example.test",
        )
        existing_guardian = Adult.all_objects.create(
            organization=self.organization,
            legal_first_name="Historical",
            last_name="Guardian",
            personal_email="historical-shared@example.test",
            is_parent=True,
        )
        self.assertEqual(
            validate_application_profile_email_claims(
                self.application, [existing_student, existing_guardian]
            ),
            "",
        )

    def test_same_person_can_use_the_same_personal_and_organization_email(self):
        draft = Student.all_objects.create(
            organization=self.organization,
            legal_first_name="Draft",
            last_name="Student",
            personal_email="same@example.test",
            org_email="same@example.test",
            is_incomplete_application_profile=True,
        )

        self.assertEqual(
            validate_application_profile_email_claims(self.application, [draft]), ""
        )

    def test_new_email_claim_detects_whitespace_in_existing_profile(self):
        Adult.objects.create(
            organization=self.organization,
            legal_first_name="Existing",
            last_name="Guardian",
            personal_email="  claimed@example.test  ",
            is_parent=True,
        )
        draft = Student.all_objects.create(
            organization=self.organization,
            legal_first_name="Draft",
            last_name="Student",
            personal_email="Claimed@example.test",
            is_incomplete_application_profile=True,
        )

        self.assertTrue(
            validate_application_profile_email_claims(self.application, [draft])
        )
        resolution = resolve_application_person_identity(
            "CLAIMED@example.test", self.organization
        )
        self.assertEqual(
            resolution.status, ApplicationIdentityProposal.MatchState.UNIQUE
        )
        self.assertEqual(resolution.profile.legal_first_name, "Existing")

    def test_relationship_proposal_rejects_a_stale_baseline(self):
        relationship = AdultStudentRelationship.objects.create(
            organization=self.organization,
            adult=self.guardian,
            student=self.student,
            relationship_to_student="parent",
        )
        proposal = ApplicationRelationshipProposal.objects.create(
            organization=self.organization,
            application=self.application,
            action=ApplicationRelationshipProposal.Action.REMOVE,
            student_profile=self.student,
            guardian_profile=self.guardian,
            baseline_application_updated_at=self.application.updated_at,
            baseline_student_revision=self.student.profile_revision,
            baseline_guardian_revision=self.guardian.profile_revision,
            baseline_relationship_id=relationship.pk,
            baseline_relationship_revision=relationship.revision,
        )
        self.assertTrue(proposal.is_baseline_current())

        relationship.specific_relationship = "updated relationship label"
        relationship.save(update_fields=["specific_relationship"])

        self.assertFalse(proposal.is_baseline_current())

    def test_null_primary_baseline_is_not_recaptured_during_decision_save(self):
        relationship = AdultStudentRelationship.objects.create(
            organization=self.organization,
            adult=self.guardian,
            student=self.student,
            relationship_to_student="parent",
        )
        proposal = ApplicationRelationshipProposal.objects.create(
            organization=self.organization,
            application=self.application,
            action=ApplicationRelationshipProposal.Action.SET_PRIMARY,
            student_profile=self.student,
            guardian_profile=self.guardian,
        )
        self.assertIsNone(proposal.baseline_primary_relationship_id)

        self.student.primary_contact_relationship = relationship
        self.student.save(update_fields=["primary_contact_relationship"])
        reviewer = get_user_model().objects.create_superuser(
            username="proposal-reviewer",
            email="proposal-reviewer@example.test",
            password=None,
        )
        proposal.decision = ApplicationRelationshipProposal.Decision.APPROVED
        proposal.reviewed_by = reviewer
        proposal.reviewed_at = timezone.now()
        proposal.save(
            update_fields=["decision", "reviewed_by", "reviewed_at", "updated_at"]
        )

        self.assertIsNone(proposal.baseline_primary_relationship_id)
        self.assertFalse(proposal.is_baseline_current())

    def test_relationship_proposal_rejects_application_progress_after_snapshot(self):
        student_draft = ApplicationStudentDraft.objects.create(
            organization=self.organization,
            application=self.application,
        )
        guardian_draft = ApplicationGuardianDraft.objects.create(
            organization=self.organization,
            application=self.application,
            slot=ApplicationGuardianDraft.Slot.PRIMARY,
            presence=ApplicationGuardianDraft.Presence.PRESENT,
            relationship_to_student="parent",
        )
        proposal = ApplicationRelationshipProposal.objects.create(
            organization=self.organization,
            application=self.application,
            action=ApplicationRelationshipProposal.Action.ADD,
            student_draft=student_draft,
            guardian_draft=guardian_draft,
            relationship_to_student="parent",
        )
        self.assertTrue(proposal.is_baseline_current())

        self.application.status = Application.Status.SUBMITTED
        self.application.save(update_fields=["status", "updated_at"])

        self.assertFalse(proposal.is_baseline_current())

    def test_relationship_proposal_rejects_changed_application_profile_link(self):
        student = Student.all_objects.create(
            organization=self.organization,
            legal_first_name="Linked",
            last_name="Student",
        )
        guardian = Adult.all_objects.create(
            organization=self.organization,
            legal_first_name="Linked",
            last_name="Guardian",
            is_parent=True,
        )
        self.application.student_profile = student
        self.application.primary_guardian_profile = guardian
        self.application.save(
            update_fields=["student_profile", "primary_guardian_profile", "updated_at"]
        )
        student_draft = ApplicationStudentDraft.objects.create(
            organization=self.organization,
            application=self.application,
        )
        guardian_draft = ApplicationGuardianDraft.objects.create(
            organization=self.organization,
            application=self.application,
            slot=ApplicationGuardianDraft.Slot.PRIMARY,
            presence=ApplicationGuardianDraft.Presence.PRESENT,
            relationship_to_student="parent",
        )
        proposal = ApplicationRelationshipProposal.objects.create(
            organization=self.organization,
            application=self.application,
            action=ApplicationRelationshipProposal.Action.ADD,
            student_draft=student_draft,
            guardian_draft=guardian_draft,
            relationship_to_student="parent",
        )
        replacement_student = Student.objects.create(
            organization=self.organization,
            legal_first_name="Replacement",
            last_name="Student",
        )

        Application.objects.filter(pk=self.application.pk).update(
            student_profile_id=replacement_student.pk
        )

        self.assertFalse(proposal.is_baseline_current())

    def test_queryset_profile_and_relationship_updates_advance_revisions(self):
        student_revision = self.student.profile_revision
        Student.objects.filter(pk=self.student.pk).update(last_name="Queryset Student")
        self.student.refresh_from_db()
        self.assertEqual(self.student.profile_revision, student_revision + 1)

        guardian_revision = self.guardian.profile_revision
        Adult.objects.filter(pk=self.guardian.pk).update(last_name="Queryset Guardian")
        self.guardian.refresh_from_db()
        self.assertEqual(self.guardian.profile_revision, guardian_revision + 1)

        relationship = AdultStudentRelationship.objects.create(
            organization=self.organization,
            adult=self.guardian,
            student=self.student,
            relationship_to_student="parent",
        )
        relationship_revision = relationship.revision
        AdultStudentRelationship.objects.filter(pk=relationship.pk).update(
            specific_relationship="updated by queryset"
        )
        relationship.refresh_from_db()
        self.assertEqual(relationship.revision, relationship_revision + 1)

        student_draft = ApplicationStudentDraft.objects.create(
            organization=self.organization,
            application=self.application,
        )
        draft_revision = student_draft.revision
        ApplicationStudentDraft.objects.filter(pk=student_draft.pk).update(
            legal_first_name="Queryset draft"
        )
        student_draft.refresh_from_db()
        self.assertEqual(student_draft.revision, draft_revision + 1)


class ApplicationProfileEditAuthorizationTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(
            name="Authorization Organization",
            slug="authorization-org",
            subdomain="authorization-org",
        )
        self.student = Student.objects.create(
            organization=self.organization,
            legal_first_name="Alex",
            last_name="Student",
            personal_email="alex@example.test",
        )
        self.guardian = Adult.objects.create(
            organization=self.organization,
            legal_first_name="Jordan",
            last_name="Guardian",
            personal_email="jordan@example.test",
            is_parent=True,
        )

    def verified_request(self, email):
        application = Application.objects.create(
            organization=self.organization,
            applicant_type=Application.Type.PARENT,
            email=email,
            email_verified_at=timezone.now(),
            status=Application.Status.EMAIL_VERIFIED,
        )
        client = Client(HTTP_HOST=f"{self.organization.subdomain}.localhost")
        response = verify_application_browser(client, application)
        request = response.wsgi_request
        request.organization = self.organization
        return application, request

    def test_verified_unique_person_can_edit_their_own_profile(self):
        application, request = self.verified_request("alex@example.test")

        result = evaluate_application_profile_edit(request, application, self.student)

        self.assertEqual(result, ApplicationProfileEditAuthorization.OWN_PROFILE)

    def test_verified_related_guardian_can_edit_student_but_unrelated_guardian_cannot(
        self,
    ):
        AdultStudentRelationship.objects.create(
            organization=self.organization,
            adult=self.guardian,
            student=self.student,
            relationship_to_student="parent",
        )
        application, request = self.verified_request("jordan@example.test")

        result = evaluate_application_profile_edit(request, application, self.student)

        self.assertEqual(result, ApplicationProfileEditAuthorization.LINKED_STUDENT)
        AdultStudentRelationship.all_objects.filter(
            organization=self.organization,
            adult=self.guardian,
            student=self.student,
        ).delete()
        result = evaluate_application_profile_edit(request, application, self.student)
        self.assertEqual(result, ApplicationProfileEditAuthorization.UNRELATED_GUARDIAN)

    def test_ambiguous_verified_email_requires_review(self):
        Adult.objects.create(
            organization=self.organization,
            legal_first_name="Another",
            last_name="Guardian",
            personal_email="alex@example.test",
            is_parent=True,
        )
        application, request = self.verified_request("alex@example.test")

        result = evaluate_application_profile_edit(request, application, self.student)

        self.assertEqual(result, ApplicationProfileEditAuthorization.IDENTITY_AMBIGUOUS)

    def test_linked_account_authorizes_only_same_organization_profile(self):
        user = get_user_model().objects.create_user(
            username="profile-owner",
            email="account@example.test",
            password=None,
        )
        self.student.user = user
        self.student.save(update_fields=["user"])
        application = Application.objects.create(
            organization=self.organization,
            email="someone-else@example.test",
        )
        client = Client(HTTP_HOST=f"{self.organization.subdomain}.localhost")
        client.force_login(user)
        response = client.get(
            reverse("apply_browser_verification", args=[application.application_id])
        )
        request = response.wsgi_request
        request.organization = self.organization

        result = evaluate_application_profile_edit(request, application, self.student)

        self.assertEqual(result, ApplicationProfileEditAuthorization.LINKED_ACCOUNT)
        foreign_student = Student.objects.create(
            organization=Organization.objects.create(
                name="Foreign Authorization Organization",
                slug="foreign-authorization-org",
                subdomain="foreign-authorization-org",
            ),
            legal_first_name="Foreign",
            last_name="Student",
        )
        self.assertEqual(
            evaluate_application_profile_edit(request, application, foreign_student),
            ApplicationProfileEditAuthorization.CROSS_ORGANIZATION,
        )

    def test_verified_guardian_cannot_edit_another_guardian_profile(self):
        Adult.objects.create(
            organization=self.organization,
            legal_first_name="Another",
            last_name="Guardian",
            personal_email="another@example.test",
            is_parent=True,
        )
        application, request = self.verified_request("another@example.test")

        result = evaluate_application_profile_edit(request, application, self.guardian)

        self.assertEqual(
            result, ApplicationProfileEditAuthorization.REQUIRES_LEAD_REVIEW
        )

    def test_profile_email_requires_a_browser_otp(self):
        application = Application.objects.create(
            organization=self.organization,
            applicant_type=Application.Type.PARENT,
            email="alex@example.test",
            email_verified_at=timezone.now(),
            status=Application.Status.EMAIL_VERIFIED,
        )
        client = Client(HTTP_HOST=f"{self.organization.subdomain}.localhost")
        response = client.get(
            reverse("apply_browser_verification", args=[application.application_id])
        )
        request = response.wsgi_request
        request.organization = self.organization

        result = evaluate_application_profile_edit(request, application, self.student)

        self.assertEqual(result, ApplicationProfileEditAuthorization.BROWSER_UNVERIFIED)
