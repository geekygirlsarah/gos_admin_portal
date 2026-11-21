"""Locked review and finalization helpers for typed application proposals."""

from dataclasses import dataclass

from django.db import models
from django.db.models.functions import Trim

from audit.events import AuditEvent
from audit.service import log_event
from programs.models import (
    Adult,
    AdultStudentRelationship,
    Enrollment,
    Student,
)

from .answers import save_structured_experience_answers
from .models import (
    Application,
    ApplicationGuardianDraft,
    ApplicationIdentityProposal,
    ApplicationRelationshipProposal,
    ApplicationStudentDraft,
)
from .services import find_application_person_candidates


@dataclass
class LockedApplicationProposalContext:
    identity_proposals: list[ApplicationIdentityProposal]
    relationship_proposals: list[ApplicationRelationshipProposal]
    students: dict[int, Student]
    adults: dict[int, Adult]
    student_drafts: dict[int, ApplicationStudentDraft]
    guardian_drafts: dict[int, ApplicationGuardianDraft]
    relationships: dict[int, AdultStudentRelationship]


class ApplicationFinalizationError(Exception):
    """A revalidation failure that must roll back final application writes."""


def lock_application_proposal_context(application, using=None):
    """Lock tenant-bound proposals and their referenced rows in stable order."""
    using = using or application._state.db or "default"
    organization_id = application.organization_id
    identity_proposals = list(
        ApplicationIdentityProposal.objects.using(using)
        .filter(application_id=application.pk, organization_id=organization_id)
        .select_for_update()
        .order_by("pk")
    )
    relationship_proposals = list(
        ApplicationRelationshipProposal.objects.using(using)
        .filter(application_id=application.pk, organization_id=organization_id)
        .select_for_update()
        .order_by("pk")
    )

    student_ids = {
        profile_id
        for profile_id in (
            application.student_profile_id,
            *(proposal.candidate_student_id for proposal in identity_proposals),
            *(proposal.target_student_id for proposal in identity_proposals),
            *(proposal.student_profile_id for proposal in relationship_proposals),
        )
        if profile_id
    }
    adult_ids = {
        profile_id
        for profile_id in (
            application.primary_guardian_profile_id,
            application.secondary_guardian_profile_id,
            *(proposal.candidate_adult_id for proposal in identity_proposals),
            *(proposal.target_adult_id for proposal in identity_proposals),
            *(proposal.guardian_profile_id for proposal in relationship_proposals),
        )
        if profile_id
    }
    relationship_ids = {
        relationship_id
        for proposal in relationship_proposals
        for relationship_id in (
            proposal.baseline_relationship_id,
            proposal.baseline_primary_relationship_id,
        )
        if relationship_id
    }

    students = {
        profile.pk: profile
        for profile in Student.all_objects.using(using)
        .select_for_update()
        .filter(organization_id=organization_id, pk__in=student_ids)
        .order_by("pk")
    }
    adults = {
        profile.pk: profile
        for profile in Adult.all_objects.using(using)
        .select_for_update()
        .filter(organization_id=organization_id, pk__in=adult_ids)
        .order_by("pk")
    }
    student_drafts = {
        draft.pk: draft
        for draft in ApplicationStudentDraft.objects.using(using)
        .select_for_update()
        .filter(
            organization_id=organization_id,
            application_id=application.pk,
        )
        .order_by("pk")
    }
    guardian_drafts = {
        draft.pk: draft
        for draft in ApplicationGuardianDraft.objects.using(using)
        .select_for_update()
        .filter(
            organization_id=organization_id,
            application_id=application.pk,
        )
        .order_by("pk")
    }
    relationship_query = AdultStudentRelationship.all_objects.using(using).filter(
        organization_id=organization_id
    )
    if relationship_ids:
        relationship_query = relationship_query.filter(
            models.Q(pk__in=relationship_ids)
            | models.Q(student_id__in=student_ids, adult_id__in=adult_ids)
        )
    elif student_ids and adult_ids:
        relationship_query = relationship_query.filter(
            student_id__in=student_ids,
            adult_id__in=adult_ids,
        )
    else:
        relationship_query = relationship_query.none()
    relationships = {
        relationship.pk: relationship
        for relationship in relationship_query.select_for_update().order_by("pk")
    }
    return LockedApplicationProposalContext(
        identity_proposals=identity_proposals,
        relationship_proposals=relationship_proposals,
        students=students,
        adults=adults,
        student_drafts=student_drafts,
        guardian_drafts=guardian_drafts,
        relationships=relationships,
    )


def proposal_review_state(application):
    """Return whether the typed proposals are resolved and baselines current."""
    if application.profile_workflow_version == 0:
        return True, ""
    if application.profile_workflow_version != 1:
        return False, "This application uses an unsupported profile workflow version."

    identity_proposals = list(
        ApplicationIdentityProposal.objects.for_organization(
            application.organization
        ).filter(application_id=application.pk)
    )
    relationship_proposals = list(
        ApplicationRelationshipProposal.objects.for_organization(
            application.organization
        ).filter(application_id=application.pk)
    )
    required_slots = {
        ApplicationIdentityProposal.SubjectSlot.STUDENT,
        ApplicationIdentityProposal.SubjectSlot.PRIMARY_GUARDIAN,
    }
    if (
        application.secondary_guardian_profile_id
        or ApplicationGuardianDraft.objects.for_organization(application.organization)
        .filter(
            application_id=application.pk,
            slot=ApplicationGuardianDraft.Slot.SECONDARY,
            presence=ApplicationGuardianDraft.Presence.PRESENT,
        )
        .exists()
    ):
        required_slots.add(ApplicationIdentityProposal.SubjectSlot.SECONDARY_GUARDIAN)
    if not required_slots.issubset(
        {proposal.subject_slot for proposal in identity_proposals}
    ):
        return (
            False,
            "Every applicant profile must have an identity decision for lead review.",
        )
    for proposal in identity_proposals:
        if proposal.decision == ApplicationIdentityProposal.Decision.PENDING:
            return (
                False,
                "Resolve every identity proposal before accepting this application.",
            )
        if not proposal.is_baseline_current():
            return False, "An identity proposal changed; reload it for lead review."
    for proposal in relationship_proposals:
        if proposal.decision == ApplicationRelationshipProposal.Decision.PENDING:
            return (
                False,
                "Resolve every relationship proposal before accepting this application.",
            )
        if not proposal.is_baseline_current():
            return False, "A relationship proposal changed; reload it for lead review."
    return True, ""


def identity_candidate_options(application, proposal):
    """Build tenant-scoped candidate options for authorized reviewer forms."""
    email = ""
    if proposal.subject_slot == ApplicationIdentityProposal.SubjectSlot.STUDENT:
        draft = (
            ApplicationStudentDraft.objects.for_organization(application.organization)
            .filter(application_id=application.pk)
            .first()
        )
        email = draft.personal_email if draft else ""
        profile_id = application.student_profile_id
        if not email and profile_id:
            profile = Student.all_objects.filter(
                pk=profile_id,
                organization_id=application.organization_id,
            ).first()
            email = (profile.personal_email or profile.org_email) if profile else ""
        if not email and application.applicant_type == Application.Type.STUDENT:
            email = application.email
    else:
        slot = (
            ApplicationGuardianDraft.Slot.PRIMARY
            if proposal.subject_slot
            == ApplicationIdentityProposal.SubjectSlot.PRIMARY_GUARDIAN
            else ApplicationGuardianDraft.Slot.SECONDARY
        )
        draft = (
            ApplicationGuardianDraft.objects.for_organization(application.organization)
            .filter(application_id=application.pk, slot=slot)
            .first()
        )
        email = draft.email if draft else ""
        profile_id = (
            application.primary_guardian_profile_id
            if slot == ApplicationGuardianDraft.Slot.PRIMARY
            else application.secondary_guardian_profile_id
        )
        if not email and profile_id:
            profile = Adult.all_objects.filter(
                pk=profile_id,
                organization_id=application.organization_id,
            ).first()
            email = (profile.personal_email or profile.org_email) if profile else ""
        if not email and slot == ApplicationGuardianDraft.Slot.PRIMARY:
            email = application.email
    return [
        {
            "profile_type": profile_type,
            "profile": profile,
            "email": profile.personal_email or profile.org_email,
        }
        for profile_type, profile in find_application_person_candidates(
            email, application.organization
        )
    ]


def _profile_for_identity_slot(proposal, context):
    if proposal.subject_slot == ApplicationIdentityProposal.SubjectSlot.STUDENT:
        if proposal.target_student_id:
            return "student_profile", context.students.get(proposal.target_student_id)
        return "student_profile", None
    if (
        proposal.subject_slot
        == ApplicationIdentityProposal.SubjectSlot.PRIMARY_GUARDIAN
    ):
        if proposal.target_adult_id:
            return "primary_guardian_profile", context.adults.get(
                proposal.target_adult_id
            )
        return "primary_guardian_profile", None
    if (
        proposal.subject_slot
        == ApplicationIdentityProposal.SubjectSlot.SECONDARY_GUARDIAN
    ):
        if proposal.target_adult_id:
            return "secondary_guardian_profile", context.adults.get(
                proposal.target_adult_id
            )
        return "secondary_guardian_profile", None
    return None, None


def _relationship_endpoints(
    application, proposal, context
) -> tuple[Student | None, Adult | None]:
    if proposal.student_profile_id:
        student = context.students.get(proposal.student_profile_id)
    else:
        student = context.students.get(application.student_profile_id)
        if proposal.student_draft_id not in context.student_drafts:
            student = None

    if proposal.guardian_profile_id:
        guardian = context.adults.get(proposal.guardian_profile_id)
    else:
        draft = context.guardian_drafts.get(proposal.guardian_draft_id)
        if draft is None:
            guardian = None
        elif draft.slot == ApplicationGuardianDraft.Slot.PRIMARY:
            guardian = context.adults.get(application.primary_guardian_profile_id)
        else:
            guardian = context.adults.get(application.secondary_guardian_profile_id)
    return student, guardian


def _validate_relationship_plan(
    application, context
) -> tuple[int | None, int | None, str]:
    student_id = application.student_profile_id
    primary_id = application.primary_guardian_profile_id
    secondary_id = application.secondary_guardian_profile_id
    relationships = {
        (relationship.student_id, relationship.adult_id)
        for relationship in context.relationships.values()
        if relationship.student_id == student_id
    }
    primary_proposals = []

    for proposal in context.relationship_proposals:
        if proposal.decision != ApplicationRelationshipProposal.Decision.APPROVED:
            continue
        student, guardian = _relationship_endpoints(application, proposal, context)
        if student is None or guardian is None or student.pk != student_id:
            return (
                None,
                None,
                "A reviewed relationship proposal no longer has valid profile targets.",
            )

        pair = (student.pk, guardian.pk)
        baseline = context.relationships.get(proposal.baseline_relationship_id)
        if proposal.action in (
            ApplicationRelationshipProposal.Action.REMOVE,
            ApplicationRelationshipProposal.Action.REPLACE,
        ):
            if baseline is None or baseline.student_id != student.pk:
                return None, None, "The reviewed relationship no longer exists."
            relationships.discard((baseline.student_id, baseline.adult_id))
            if baseline.adult_id == primary_id:
                primary_id = None
            if baseline.adult_id == secondary_id:
                secondary_id = None
        if proposal.action in (
            ApplicationRelationshipProposal.Action.ADD,
            ApplicationRelationshipProposal.Action.REPLACE,
        ):
            if pair in relationships:
                return None, None, "The approved relationship already exists."
            relationships.add(pair)
            if proposal.action == ApplicationRelationshipProposal.Action.REPLACE:
                if (
                    baseline
                    and baseline.adult_id == application.primary_guardian_profile_id
                ):
                    primary_id = guardian.pk
                if (
                    baseline
                    and baseline.adult_id == application.secondary_guardian_profile_id
                ):
                    secondary_id = guardian.pk
        elif proposal.action == ApplicationRelationshipProposal.Action.SET_PRIMARY:
            primary_proposals.append((student, guardian))

    for student, guardian in primary_proposals:
        if (student.pk, guardian.pk) not in relationships:
            return (
                None,
                None,
                "A primary contact must have an approved guardian relationship.",
            )
        old_primary = primary_id
        primary_id = guardian.pk
        if secondary_id == guardian.pk:
            secondary_id = old_primary if old_primary != guardian.pk else None

    if not primary_id or (student_id, primary_id) not in relationships:
        return (
            None,
            None,
            "The accepted application needs an approved primary guardian relationship.",
        )
    if secondary_id and (student_id, secondary_id) not in relationships:
        return (
            None,
            None,
            "Every linked guardian needs an approved student relationship.",
        )
    return primary_id, secondary_id, ""


def validate_application_profile_email_claims(application, profiles):
    """Reject new same-organization email claims without rewriting history."""
    profiles = [profile for profile in profiles if profile is not None]
    student_ids = [profile.pk for profile in profiles if isinstance(profile, Student)]
    adult_ids = [profile.pk for profile in profiles if isinstance(profile, Adult)]
    address_owners = {}
    for profile in profiles:
        addresses = {
            (value or "").strip().casefold()
            for value in (profile.personal_email, profile.org_email)
        }
        for address in addresses - {""}:
            address_owners.setdefault(address, []).append(profile)

    for owners in address_owners.values():
        if len(owners) > 1 and any(
            owner.is_incomplete_application_profile for owner in owners
        ):
            return "Student and guardian emails must be distinct before acceptance."

    for profile in profiles:
        if not profile.is_incomplete_application_profile:
            continue
        for value in (profile.personal_email, profile.org_email):
            address = (value or "").strip()
            if not address:
                continue
            email_filter = models.Q(
                normalized_personal_email__iexact=address
            ) | models.Q(normalized_org_email__iexact=address)
            student_match = (
                Student.all_objects.filter(organization_id=application.organization_id)
                .alias(
                    normalized_personal_email=Trim("personal_email"),
                    normalized_org_email=Trim("org_email"),
                )
                .filter(email_filter)
            )
            adult_match = (
                Adult.all_objects.filter(organization_id=application.organization_id)
                .alias(
                    normalized_personal_email=Trim("personal_email"),
                    normalized_org_email=Trim("org_email"),
                )
                .filter(email_filter)
            )
            if isinstance(profile, Student):
                student_match = student_match.exclude(pk__in=student_ids)
            else:
                adult_match = adult_match.exclude(pk__in=adult_ids)
            if student_match.exists() or adult_match.exists():
                return "A profile email matches another person in this organization; resolve the identity first."
    return ""


def _apply_relationship_proposals(application, context, request) -> None:
    pending_primary = []
    for proposal in context.relationship_proposals:
        if proposal.decision != ApplicationRelationshipProposal.Decision.APPROVED:
            continue
        student, guardian = _relationship_endpoints(application, proposal, context)
        if student is None or guardian is None:
            raise ApplicationFinalizationError(
                "A reviewed relationship proposal no longer has valid profile targets."
            )
        if (
            student.is_incomplete_application_profile
            or guardian.is_incomplete_application_profile
        ):
            raise ApplicationFinalizationError(
                "Profiles must be activated before an approved relationship can be applied."
            )

        if proposal.action == ApplicationRelationshipProposal.Action.SET_PRIMARY:
            pending_primary.append((proposal, student, guardian))
            continue

        baseline = context.relationships.get(proposal.baseline_relationship_id)
        if proposal.action in (
            ApplicationRelationshipProposal.Action.REMOVE,
            ApplicationRelationshipProposal.Action.REPLACE,
        ):
            if baseline is None:
                raise ApplicationFinalizationError(
                    "The reviewed relationship no longer exists."
                )
            before = {
                "relationship_id": baseline.pk,
                "student_id": baseline.student_id,
                "adult_id": baseline.adult_id,
            }
            baseline.delete()
            context.relationships.pop(baseline.pk, None)
            log_event(
                request=request,
                event=AuditEvent.GUARDIAN_REMOVED,
                resource=student,
                before=before,
                after={"application_id": application.application_id},
                notes="Application-approved guardian relationship change.",
            )

        if proposal.action in (
            ApplicationRelationshipProposal.Action.ADD,
            ApplicationRelationshipProposal.Action.REPLACE,
        ):
            if AdultStudentRelationship.all_objects.filter(
                organization_id=application.organization_id,
                student_id=student.pk,
                adult_id=guardian.pk,
            ).exists():
                raise ApplicationFinalizationError(
                    "The approved relationship already exists; review the application again."
                )
            specific_relationship = ""
            if proposal.guardian_draft_id:
                draft = context.guardian_drafts.get(proposal.guardian_draft_id)
                specific_relationship = draft.specific_relationship or ""
            relationship = AdultStudentRelationship.objects.create(
                organization_id=application.organization_id,
                student=student,
                adult=guardian,
                relationship_to_student=proposal.relationship_to_student,
                specific_relationship=specific_relationship,
            )
            context.relationships[relationship.pk] = relationship
            log_event(
                request=request,
                event=AuditEvent.GUARDIAN_ADDED,
                resource=student,
                after={
                    "relationship_id": relationship.pk,
                    "application_id": application.application_id,
                },
                notes="Application-approved guardian relationship change.",
            )

    for proposal, student, guardian in pending_primary:
        relationship = (
            AdultStudentRelationship.objects.for_organization(application.organization)
            .filter(student_id=student.pk, adult_id=guardian.pk)
            .first()
        )
        if relationship is None:
            raise ApplicationFinalizationError(
                "A primary contact must have an approved guardian relationship."
            )
        old_relationship_id = student.primary_contact_relationship_id
        if old_relationship_id != relationship.pk:
            student.primary_contact_relationship = relationship
            student.save(update_fields=["primary_contact_relationship", "updated_at"])
            log_event(
                request=request,
                event=AuditEvent.CONTACT_INFO_UPDATED,
                resource=student,
                before={"primary_relationship_id": old_relationship_id},
                after={
                    "primary_relationship_id": relationship.pk,
                    "application_id": application.application_id,
                },
                notes="Primary guardian contact updated at application acceptance.",
            )


def finalize_profile_workflow_application(application, request, using=None):
    """Revalidate and atomically activate a typed student/guardian workflow."""
    if application.profile_workflow_version == 0:
        return True, None, ""
    if application.profile_workflow_version != 1:
        return (
            False,
            None,
            "This application uses an unsupported profile workflow version.",
        )
    if application.status != Application.Status.SUBMITTED:
        return False, None, "The application must be submitted before acceptance."
    if application.applicant_type not in (
        Application.Type.STUDENT,
        Application.Type.PARENT,
    ):
        return (
            False,
            None,
            "Profile finalization is only available for student applications.",
        )
    if not application.program_id:
        return False, None, "A program is required before accepting this application."

    context = lock_application_proposal_context(application, using=using)
    resolved, reason = proposal_review_state(application)
    if not resolved:
        return False, None, reason

    identity_by_slot = {
        proposal.subject_slot: proposal for proposal in context.identity_proposals
    }
    required_slots = {
        ApplicationIdentityProposal.SubjectSlot.STUDENT,
        ApplicationIdentityProposal.SubjectSlot.PRIMARY_GUARDIAN,
    }
    if application.secondary_guardian_profile_id or any(
        draft.slot == ApplicationGuardianDraft.Slot.SECONDARY
        and draft.presence == ApplicationGuardianDraft.Presence.PRESENT
        for draft in context.guardian_drafts.values()
    ):
        required_slots.add(ApplicationIdentityProposal.SubjectSlot.SECONDARY_GUARDIAN)
    if not required_slots.issubset(identity_by_slot):
        return (
            False,
            None,
            "Every applicant profile must have a resolved identity decision.",
        )

    for slot in required_slots:
        proposal = identity_by_slot[slot]
        if proposal.decision == ApplicationIdentityProposal.Decision.CONFIRMED:
            field_name, target = _profile_for_identity_slot(proposal, context)
            if target is None or target.organization_id != application.organization_id:
                return (
                    False,
                    None,
                    "A confirmed identity target is no longer available.",
                )
            setattr(application, field_name, target)
        elif proposal.decision not in (
            ApplicationIdentityProposal.Decision.NO_MATCH,
            ApplicationIdentityProposal.Decision.REJECTED,
        ):
            return False, None, "Identity proposals are unresolved."

    primary_guardian_id, secondary_guardian_id, relationship_error = (
        _validate_relationship_plan(application, context)
    )
    if relationship_error:
        return False, None, relationship_error
    if primary_guardian_id is None:
        return False, None, "The accepted application needs a primary guardian profile."
    final_profiles = {
        ApplicationIdentityProposal.SubjectSlot.STUDENT: context.students.get(
            application.student_profile_id
        ),
        ApplicationIdentityProposal.SubjectSlot.PRIMARY_GUARDIAN: context.adults.get(
            primary_guardian_id
        ),
        ApplicationIdentityProposal.SubjectSlot.SECONDARY_GUARDIAN: (
            context.adults.get(secondary_guardian_id) if secondary_guardian_id else None
        ),
    }
    for slot, linked in final_profiles.items():
        if linked is None:
            continue
        proposal = identity_by_slot.get(slot)
        if proposal is None:
            return False, None, "Every accepted profile needs an identity decision."
        if proposal.decision == ApplicationIdentityProposal.Decision.CONFIRMED:
            _, target = _profile_for_identity_slot(proposal, context)
            if target != linked:
                return (
                    False,
                    None,
                    "The accepted profile does not match the reviewed identity.",
                )
        elif not linked.is_incomplete_application_profile:
            return (
                False,
                None,
                "An existing profile requires a confirmed identity decision.",
            )
    application.primary_guardian_profile_id = primary_guardian_id
    application.secondary_guardian_profile_id = secondary_guardian_id

    student = context.students.get(application.student_profile_id)
    primary_guardian = context.adults.get(primary_guardian_id)
    secondary_guardian = (
        context.adults.get(secondary_guardian_id)
        if secondary_guardian_id is not None
        else None
    )
    if student is None or primary_guardian is None:
        return False, None, "The student and primary guardian profiles are required."
    if any(
        profile.organization_id != application.organization_id
        for profile in (student, primary_guardian, secondary_guardian)
        if profile is not None
    ):
        return False, None, "Application profiles must belong to this organization."
    email_error = validate_application_profile_email_claims(
        application,
        [student, primary_guardian, secondary_guardian],
    )
    if email_error:
        return False, None, email_error

    for profile, model, field_name in (
        (student, Student, "student"),
        (primary_guardian, Adult, "primary guardian"),
        (secondary_guardian, Adult, "secondary guardian"),
    ):
        if profile is None or not profile.is_incomplete_application_profile:
            continue
        if profile.user_id:
            return (
                False,
                None,
                f"The incomplete {field_name} profile is linked to an account.",
            )
        if (
            model is Student
            and Enrollment.all_objects.filter(student_id=profile.pk).exists()
        ):
            return (
                False,
                None,
                "An incomplete student profile already has an enrollment.",
            )
        if (
            model is Student
            and AdultStudentRelationship.all_objects.filter(
                student_id=profile.pk
            ).exists()
        ):
            return (
                False,
                None,
                "An incomplete student profile already has guardian relationships.",
            )
        if (
            model is Adult
            and AdultStudentRelationship.all_objects.filter(
                adult_id=profile.pk
            ).exists()
        ):
            return (
                False,
                None,
                "An incomplete guardian profile already has relationships.",
            )

    database = using or application._state.db or "default"
    existing_enrollment = (
        Enrollment.all_objects.using(database)
        .select_for_update()
        .filter(
            organization_id=application.organization_id,
            student_id=student.pk,
            program_id=application.program_id,
        )
        .first()
    )

    application.save(
        update_fields=[
            "student_profile",
            "primary_guardian_profile",
            "secondary_guardian_profile",
            "updated_at",
        ]
    )

    for profile in (student, primary_guardian, secondary_guardian):
        if profile is None:
            continue
        fields = []
        if profile.is_incomplete_application_profile:
            profile.is_incomplete_application_profile = False
            fields.append("is_incomplete_application_profile")
        if isinstance(profile, Adult) and not profile.is_parent:
            profile.is_parent = True
            fields.append("is_parent")
        if fields:
            profile.save(update_fields=[*fields, "updated_at"])

    _apply_relationship_proposals(application, context, request)

    for guardian in (primary_guardian, secondary_guardian):
        if guardian is None:
            continue
        if (
            not AdultStudentRelationship.objects.for_organization(
                application.organization
            )
            .filter(student_id=student.pk, adult_id=guardian.pk)
            .exists()
        ):
            raise ApplicationFinalizationError(
                "Every linked guardian needs an approved student relationship."
            )

    if existing_enrollment is None:
        Enrollment.objects.create(
            organization_id=application.organization_id,
            student=student,
            program_id=application.program_id,
        )
        log_event(
            request=request,
            event=AuditEvent.ENROLLMENT_CHANGED,
            resource=student,
            after={
                "application_id": application.application_id,
                "program_id": application.program_id,
            },
            notes="Student enrolled when the application was accepted.",
        )
    elif not existing_enrollment.active:
        existing_enrollment.active = True
        existing_enrollment.save(update_fields=["active"])
        log_event(
            request=request,
            event=AuditEvent.ENROLLMENT_CHANGED,
            resource=student,
            before={"active": False},
            after={"active": True, "application_id": application.application_id},
            notes="Student enrollment reactivated when the application was accepted.",
        )

    try:
        save_structured_experience_answers(application, student)
    except ValueError as exc:
        raise ApplicationFinalizationError(str(exc)) from exc
    return True, student, ""
