"""Shared merge helper utilities for Parents, Students, Schools, and Visitors.

Consolidates record consolidation, field carry-over, relationship transfer,
and related entity reassignments into reusable, atomic services.
"""

from __future__ import annotations

import datetime
from typing import Any, Iterable, Sequence

from django.db import models, transaction

from .accounts import transfer_user_account


def carry_over_missing_fields(
    keep: models.Model,
    source: models.Model,
    handled_special_fields: set[str] | None = None,
) -> bool:
    """Copy non-empty fields from ``source`` onto ``keep`` where ``keep`` is empty.

    - BooleanField: Promotes False -> True if source is True.
    - Other fields: Fills None or empty string from source if source is non-empty.
    - Fields in ``handled_special_fields`` are skipped.

    Returns True if ``keep`` was modified.
    """
    if handled_special_fields is None:
        handled_special_fields = {"id", "created_at", "updated_at"}

    changed = False

    for field in keep._meta.fields:
        field_name = field.name
        if field_name in handled_special_fields:
            continue

        keep_val = getattr(keep, field_name, None)
        source_val = getattr(source, field_name, None)

        if isinstance(field, models.BooleanField):
            if not keep_val and source_val:
                setattr(keep, field_name, True)
                changed = True
        else:
            is_keep_empty = keep_val is None or (
                isinstance(keep_val, str) and not keep_val.strip()
            )
            is_source_filled = source_val is not None and (
                not isinstance(source_val, str) or bool(str(source_val).strip())
            )
            if is_keep_empty and is_source_filled:
                setattr(keep, field_name, source_val)
                changed = True

    return changed


# -----------------------------------------------------------------------------
# School Merge
# -----------------------------------------------------------------------------


def perform_school_merge(keep: Any, source: Any) -> dict[str, Any]:
    """Merge ``source`` school into ``keep`` school atomically."""
    from programs.models import Student

    source_pk = source.pk
    source_name = source.name
    keep_pk = keep.pk
    keep_name = keep.name

    with transaction.atomic():
        contact_fields = [
            "district",
            "street_address",
            "city",
            "state",
            "zip_code",
        ]
        changed = False
        for field in contact_fields:
            if not getattr(keep, field) and getattr(source, field):
                setattr(keep, field, getattr(source, field))
                changed = True
        if changed:
            keep.save()

        Student.objects.filter(school=source).update(school=keep)
        source.delete()

    return {
        "source_pk": source_pk,
        "source_name": source_name,
        "keep_pk": keep_pk,
        "keep_name": keep_name,
    }


# -----------------------------------------------------------------------------
# Parent / Adult Merge
# -----------------------------------------------------------------------------


def _transfer_parent_relationships(keep: Any, source: Any) -> None:
    from programs.models import AdultStudentRelationship, Student

    for rel in list(AdultStudentRelationship.objects.filter(adult=source)):
        existing = AdultStudentRelationship.objects.filter(
            adult=keep, student=rel.student
        ).first()
        if existing:
            if not existing.specific_relationship and rel.specific_relationship:
                existing.specific_relationship = rel.specific_relationship
                existing.save(update_fields=["specific_relationship"])
            Student.objects.filter(primary_contact_relationship=rel).update(
                primary_contact_relationship=existing
            )
            Student.objects.filter(secondary_contact_relationship=rel).update(
                secondary_contact_relationship=existing
            )
            rel.delete()
        else:
            rel.adult = keep
            rel.save(update_fields=["adult"])


def _transfer_parent_background_checks(keep: Any, source: Any) -> None:
    from programs.models import BackgroundCheck

    for bg in list(BackgroundCheck.objects.filter(adult=source)):
        existing = BackgroundCheck.objects.filter(
            adult=keep, check_type=bg.check_type
        ).first()
        if existing:
            updated = False
            if not existing.cleared and bg.cleared:
                existing.cleared = True
                updated = True
            if not existing.obtained_date and bg.obtained_date:
                existing.obtained_date = bg.obtained_date
                updated = True
            if updated:
                existing.save()
            bg.delete()
        else:
            bg.adult = keep
            bg.save(update_fields=["adult"])


def _transfer_parent_agreements(keep: Any, source: Any) -> None:
    from programs.models import (
        MentorAgreementAcceptance,
        MentorAgreementSubmission,
    )

    for acc in list(MentorAgreementAcceptance.objects.filter(adult=source)):
        if not MentorAgreementAcceptance.objects.filter(
            adult=keep, agreement=acc.agreement
        ).exists():
            acc.adult = keep
            acc.save(update_fields=["adult"])
        else:
            acc.delete()

    for sub in list(MentorAgreementSubmission.objects.filter(adult=source)):
        if not MentorAgreementSubmission.objects.filter(
            adult=keep, agreement=sub.agreement
        ).exists():
            sub.adult = keep
            sub.save(update_fields=["adult"])
        else:
            sub.delete()


def _transfer_parent_outreach_signups(keep: Any, source: Any) -> None:
    from outreach.models import OutreachMentorSignup

    for signup in list(OutreachMentorSignup.objects.filter(adult=source)):
        if not OutreachMentorSignup.objects.filter(
            adult=keep, shift=signup.shift
        ).exists():
            signup.adult = keep
            signup.save(update_fields=["adult"])
        else:
            signup.delete()


def _transfer_parent_rfid_and_attendance(keep: Any, source: Any) -> None:
    from attendance.models import AttendanceEvent, AttendanceSession, RFIDCard
    from programs.models import Adult, SlidingScale, Student

    for card in list(RFIDCard.objects.filter(adult=source)):
        if not RFIDCard.objects.filter(adult=keep, uid=card.uid).exists():
            card.adult = keep
            card.save(update_fields=["adult"])
        else:
            card.delete()

    SlidingScale.objects.filter(applied_by=source).update(applied_by=keep)
    AttendanceSession.objects.filter(adult=source).update(adult=keep)
    AttendanceEvent.objects.filter(adult=source).update(adult=keep)
    Adult.objects.filter(andrew_id_sponsor=source).update(andrew_id_sponsor=keep)
    Student.objects.filter(andrew_id_sponsor=source).update(andrew_id_sponsor=keep)


def _transfer_parent_related_records(keep: Any, source: Any) -> None:
    _transfer_parent_background_checks(keep, source)
    _transfer_parent_agreements(keep, source)
    _transfer_parent_outreach_signups(keep, source)
    _transfer_parent_rfid_and_attendance(keep, source)


def _carry_over_missing_parent_fields(keep: Any, source: Any) -> bool:
    keep_had_phone = bool(keep.phone_number and str(keep.phone_number).strip())
    keep_had_address = bool(
        (keep.address and str(keep.address).strip())
        or (keep.city and str(keep.city).strip())
    )
    keep_had_mentor_role = bool(keep.is_mentor and keep.role and keep.role != "mentor")

    changed = False

    # 1. OneToOne student_record (Alumni profile link)
    if keep.student_record_id is None and source.student_record_id is not None:
        rec = source.student_record
        source.student_record = None
        source.save(update_fields=["student_record"])
        keep.student_record = rec
        changed = True

    # 2. Andrew ID sponsor FK
    if keep.andrew_id_sponsor_id is None and source.andrew_id_sponsor_id is not None:
        if source.andrew_id_sponsor_id == source.pk:
            keep.andrew_id_sponsor = keep
        else:
            keep.andrew_id_sponsor = source.andrew_id_sponsor
        changed = True

    # 3. Photo (ImageField)
    if not bool(keep.photo) and bool(source.photo):
        keep.photo = source.photo
        changed = True

    # 4. Defaulted choice fields
    if not keep_had_phone and source.phone_type and source.phone_number:
        keep.phone_type = source.phone_type
        changed = True
    if not keep_had_address and (source.address or source.city) and source.state:
        keep.state = source.state
        changed = True
    if (
        not keep_had_mentor_role
        and source.is_mentor
        and source.role
        and source.role != "mentor"
    ):
        keep.role = source.role
        changed = True

    # 5. Generic field carry over
    handled_special_fields = {
        "id",
        "user",
        "is_parent",
        "is_mentor",
        "is_alumni",
        "student_record",
        "andrew_id_sponsor",
        "photo",
        "phone_type",
        "state",
        "role",
        "created_at",
        "updated_at",
    }
    changed = (
        carry_over_missing_fields(
            keep, source, handled_special_fields=handled_special_fields
        )
        or changed
    )
    return changed


def _merge_parent_role_flags(keep: Any, source: Any) -> bool:
    changed = False
    for flag in ("is_parent", "is_mentor", "is_alumni"):
        if getattr(source, flag) and not getattr(keep, flag):
            setattr(keep, flag, True)
            changed = True
    return changed


def perform_parent_merge(keep: Any, source: Any) -> dict[str, Any]:
    """Merge ``source`` Adult/Parent into ``keep`` Adult/Parent atomically."""
    source_pk = source.pk
    source_name = getattr(source, "display_name", str(source))
    keep_pk = keep.pk
    keep_name = getattr(keep, "display_name", str(keep))

    with transaction.atomic():
        _transfer_parent_relationships(keep, source)
        _transfer_parent_related_records(keep, source)
        changed = _carry_over_missing_parent_fields(keep, source)
        changed = _merge_parent_role_flags(keep, source) or changed
        changed = transfer_user_account(keep, source) or changed
        if changed:
            keep.save()

        source.delete()

    return {
        "source_pk": source_pk,
        "source_name": source_name,
        "keep_pk": keep_pk,
        "keep_name": keep_name,
    }


# -----------------------------------------------------------------------------
# Student Merge
# -----------------------------------------------------------------------------


def _transfer_student_relationships(keep: Any, source: Any) -> bool:
    from programs.models import AdultStudentRelationship

    changed = False

    for rel in list(AdultStudentRelationship.objects.filter(student=source)):
        existing = AdultStudentRelationship.objects.filter(
            adult=rel.adult, student=keep
        ).first()
        if existing:
            if not existing.specific_relationship and rel.specific_relationship:
                existing.specific_relationship = rel.specific_relationship
                existing.save(update_fields=["specific_relationship"])
            rel.delete()
        else:
            rel.student = keep
            rel.save(update_fields=["student"])
            existing = rel

        if source.primary_contact_relationship_id == rel.pk:
            if keep.primary_contact_relationship_id is None:
                keep.primary_contact_relationship = existing
                changed = True
        elif source.secondary_contact_relationship_id == rel.pk:
            if keep.secondary_contact_relationship_id is None:
                keep.secondary_contact_relationship = existing
                changed = True

    return changed


def _transfer_student_enrollments(keep: Any, source: Any) -> None:
    from programs.models import Enrollment

    for enr in list(Enrollment.objects.filter(student=source)):
        existing = Enrollment.objects.filter(student=keep, program=enr.program).first()
        if existing:
            updated = False
            if not existing.team_id and enr.team_id:
                existing.team = enr.team
                updated = True
            if not existing.crew_id and enr.crew_id:
                existing.crew = enr.crew
                updated = True
            if not existing.subteam_id and enr.subteam_id:
                existing.subteam = enr.subteam
                updated = True
            if not existing.active and enr.active:
                existing.active = True
                updated = True
            if not existing.clearance_due and enr.clearance_due:
                existing.clearance_due = True
                updated = True
            if updated:
                existing.save()
            enr.delete()
        else:
            enr.student = keep
            enr.save(update_fields=["student"])


def _transfer_student_financials(keep: Any, source: Any) -> None:
    from programs.models import FeeAssignment, Payment, SlidingScale

    Payment.objects.filter(student=source).update(student=keep)

    for fa in list(FeeAssignment.objects.filter(student=source)):
        existing = FeeAssignment.objects.filter(student=keep, fee=fa.fee).first()
        if existing:
            if not existing.notes and fa.notes:
                existing.notes = fa.notes
                existing.save(update_fields=["notes"])
            fa.delete()
        else:
            fa.student = keep
            fa.save(update_fields=["student"])

    for ss in list(SlidingScale.objects.filter(student=source)):
        if not SlidingScale.objects.filter(
            student=keep, date=ss.date, percent=ss.percent
        ).exists():
            ss.student = keep
            ss.save(update_fields=["student"])
        else:
            ss.delete()


def _transfer_student_documents_and_checks(keep: Any, source: Any) -> None:
    from programs.models import BackgroundCheck, StudentDocument

    for sdoc in list(StudentDocument.objects.filter(student=source)):
        existing = StudentDocument.objects.filter(
            student=keep, program_document=sdoc.program_document
        ).first()
        if existing:
            if not existing.file and sdoc.file:
                existing.file = sdoc.file
                existing.save(update_fields=["file"])
            sdoc.delete()
        else:
            sdoc.student = keep
            sdoc.save(update_fields=["student"])

    for bg in list(BackgroundCheck.objects.filter(student=source)):
        existing = BackgroundCheck.objects.filter(
            student=keep, check_type=bg.check_type
        ).first()
        if existing:
            updated = False
            if not existing.cleared and bg.cleared:
                existing.cleared = True
                updated = True
            if not existing.obtained_date and bg.obtained_date:
                existing.obtained_date = bg.obtained_date
                updated = True
            if updated:
                existing.save()
            bg.delete()
        else:
            bg.student = keep
            bg.save(update_fields=["student"])


def _transfer_student_attendance_records(keep: Any, source: Any) -> None:
    from attendance.models import (
        AttendanceEvent,
        AttendanceSession,
        DigitalSignout,
        RFIDCard,
        StudentPresence,
    )

    for card in list(RFIDCard.objects.filter(student=source)):
        if not RFIDCard.objects.filter(student=keep, uid=card.uid).exists():
            card.student = keep
            card.save(update_fields=["student"])
        else:
            card.delete()

    AttendanceSession.objects.filter(student=source).update(student=keep)
    AttendanceEvent.objects.filter(student=source).update(student=keep)

    for presence in list(StudentPresence.objects.filter(student=source)):
        existing = StudentPresence.objects.filter(
            student=keep, program=presence.program, date=presence.date
        ).first()
        if existing:
            if (
                presence.status == StudentPresence.ABSENT
                and existing.status != StudentPresence.ABSENT
            ):
                existing.status = StudentPresence.ABSENT
                existing.save(update_fields=["status"])
            presence.delete()
        else:
            presence.student = keep
            presence.save(update_fields=["student"])

    DigitalSignout.objects.filter(student=source).update(student=keep)


def _transfer_student_outreach_signups(keep: Any, source: Any) -> None:
    from applications.models import Application
    from outreach.models import OutreachSignup

    Application.objects.filter(converted_student=source).update(converted_student=keep)

    for signup in list(OutreachSignup.objects.filter(student=source)):
        existing = OutreachSignup.objects.filter(
            student=keep, shift=signup.shift
        ).first()
        if existing:
            updated = False
            if (
                signup.role == OutreachSignup.CHAMPION
                and existing.role != OutreachSignup.CHAMPION
            ):
                existing.role = OutreachSignup.CHAMPION
                updated = True
            if signup.checked_in_at and not existing.checked_in_at:
                existing.checked_in_at = signup.checked_in_at
                updated = True
            if signup.checked_out_at and not existing.checked_out_at:
                existing.checked_out_at = signup.checked_out_at
                updated = True
            if updated:
                existing.save()
            signup.delete()
        else:
            signup.student = keep
            signup.save(update_fields=["student"])


def _transfer_student_badges_and_alumni(keep: Any, source: Any) -> None:
    from badges.models import StudentBadge
    from programs.models import Adult

    for badge_award in list(StudentBadge.objects.filter(student=source)):
        if not StudentBadge.objects.filter(
            student=keep, badge=badge_award.badge
        ).exists():
            badge_award.student = keep
            badge_award.save(update_fields=["student"])
        else:
            badge_award.delete()

    for adult in list(Adult.objects.filter(student_record=source)):
        if not Adult.objects.filter(student_record=keep).exists():
            adult.student_record = keep
            adult.save(update_fields=["student_record"])
        else:
            adult.student_record = None
            adult.save(update_fields=["student_record"])


def _transfer_student_related_records(keep: Any, source: Any) -> None:
    _transfer_student_enrollments(keep, source)
    _transfer_student_financials(keep, source)
    _transfer_student_documents_and_checks(keep, source)
    _transfer_student_attendance_records(keep, source)
    _transfer_student_outreach_signups(keep, source)
    _transfer_student_badges_and_alumni(keep, source)


def _carry_over_missing_student_fields(keep: Any, source: Any) -> bool:
    keep_had_phone = bool(keep.phone_number and str(keep.phone_number).strip())
    keep_had_address = bool(
        (keep.address and str(keep.address).strip())
        or (keep.city and str(keep.city).strip())
    )
    keep_had_dob = bool(
        keep.date_of_birth and keep.date_of_birth != datetime.date(1900, 1, 1)
    )
    source_has_dob = bool(
        source.date_of_birth and source.date_of_birth != datetime.date(1900, 1, 1)
    )

    changed = False

    # 1. Photo
    if not bool(keep.photo) and bool(source.photo):
        keep.photo = source.photo
        changed = True

    # 2. Date of birth
    if not keep_had_dob and source_has_dob:
        keep.date_of_birth = source.date_of_birth
        changed = True

    # 3. Defaulted choice fields
    if (
        not keep_had_phone
        and source.phone_number
        and str(source.phone_number).strip()
        and source.phone_type
    ):
        keep.phone_type = source.phone_type
        changed = True

    if (
        not keep_had_address
        and (
            (source.address and str(source.address).strip())
            or (source.city and str(source.city).strip())
        )
        and source.state
    ):
        keep.state = source.state
        changed = True

    # 4. Foreign keys
    if keep.school_id is None and source.school_id is not None:
        keep.school = source.school
        changed = True

    if keep.andrew_id_sponsor_id is None and source.andrew_id_sponsor_id is not None:
        keep.andrew_id_sponsor = source.andrew_id_sponsor
        changed = True

    # 5. M2M race_ethnicities
    if source.pk and keep.pk:
        for re in source.race_ethnicities.all():
            if not keep.race_ethnicities.filter(pk=re.pk).exists():
                keep.race_ethnicities.add(re)

    # 6. Generic field carry over
    handled_special_fields = {
        "id",
        "user",
        "school",
        "andrew_id_sponsor",
        "primary_contact_relationship",
        "secondary_contact_relationship",
        "photo",
        "date_of_birth",
        "phone_type",
        "state",
        "created_at",
        "updated_at",
    }
    changed = (
        carry_over_missing_fields(
            keep, source, handled_special_fields=handled_special_fields
        )
        or changed
    )
    return changed


def perform_student_merge(keep: Any, source: Any) -> dict[str, Any]:
    """Merge ``source`` Student into ``keep`` Student atomically."""
    source_pk = source.pk
    source_name = getattr(source, "display_name", str(source))
    keep_pk = keep.pk
    keep_name = getattr(keep, "display_name", str(keep))

    with transaction.atomic():
        changed = _transfer_student_relationships(keep, source)
        _transfer_student_related_records(keep, source)
        changed = _carry_over_missing_student_fields(keep, source) or changed
        changed = transfer_user_account(keep, source) or changed
        if changed:
            keep.save()

        # Clear unique fields before deleting source
        source.personal_email = None
        source.andrew_email = None
        source.delete()

    return {
        "source_pk": source_pk,
        "source_name": source_name,
        "keep_pk": keep_pk,
        "keep_name": keep_name,
    }


# -----------------------------------------------------------------------------
# Visitor Merge
# -----------------------------------------------------------------------------


def perform_visitor_merge(
    selected_names: Sequence[str] | Iterable[str], target_name: str
) -> int:
    """Merge visitor sessions and events with names in ``selected_names`` into ``target_name``."""
    from attendance.models import AttendanceEvent, AttendanceSession

    names_list = list(selected_names)
    with transaction.atomic():
        sessions_updated = AttendanceSession.objects.filter(
            student__isnull=True,
            adult__isnull=True,
            visitor_name__in=names_list,
        ).update(visitor_name=target_name)

        events_updated = AttendanceEvent.objects.filter(
            student__isnull=True,
            adult__isnull=True,
            visitor_name__in=names_list,
        ).update(visitor_name=target_name)

    return sessions_updated + events_updated
