"""Preserve answers captured by the existing student application wizard."""

import json
from uuid import UUID

from django.db import transaction
from django.utils.crypto import salted_hmac

from programs.models import Program

from .models import (
    Application,
    ProgramApplicationQuestion,
    StudentApplicationAnswer,
)

EXPERIENCE_QUESTIONS = {
    "interest_reason": (
        "Why are you interested in participating in this program this season?",
        "Why is your child interested in participating in this program this season?",
    ),
    "hoped_gains": (
        "What do you hope to gain from the experience?",
        "What does your child hope to gain from the experience?",
    ),
    "prior_robotics_experience": (
        "What prior robotics experience do you have?",
        "What prior robotics experience does your child have?",
    ),
    "referral_source": (
        "How did you hear about this program?",
        "How did you hear about this program?",
    ),
}

CUSTOM_QUESTION_SNAPSHOTS_KEY = "_custom_question_snapshots"


def build_custom_question_snapshot(application):
    """Build the active question set for this application's program."""
    if not application.program_id:
        raise ValueError("An application program is required for custom questions.")
    if application.program.organization_id != application.organization_id:
        raise ValueError("The application program does not belong to its organization.")
    questions = ProgramApplicationQuestion.objects.for_organization(
        application.organization
    ).filter(
        program_id=application.program_id,
        program__organization_id=application.organization_id,
        is_active=True,
    )
    return [
        {
            "question_id": str(question.question_id),
            "question_text": question.text,
            "required": question.is_required,
            "answer": "",
        }
        for question in questions
    ]


def validate_custom_question_snapshot(snapshot, require_answers=False):
    """Reject malformed snapshots and, when requested, missing required answers."""
    if not isinstance(snapshot, list):
        raise ValueError("Custom application questions are malformed.")
    seen = set()
    for item in snapshot:
        if not isinstance(item, dict):
            raise ValueError("Custom application question is malformed.")
        question_id = item.get("question_id")
        try:
            if not isinstance(question_id, str):
                raise ValueError
            canonical_id = str(UUID(question_id))
        except ValueError as exc:
            raise ValueError("Custom application question is malformed.") from exc
        if (
            canonical_id in seen
            or not isinstance(item.get("question_text"), str)
            or not item["question_text"].strip()
            or not isinstance(item.get("required"), bool)
            or not isinstance(item.get("answer"), str)
        ):
            raise ValueError("Custom application question is malformed.")
        seen.add(canonical_id)
        if require_answers and item["required"] and not item["answer"].strip():
            raise ValueError("Required custom questions are unanswered.")


def experience_form_version(application):
    """Return a signed version of the Step 6 state relevant to the form."""
    data = application.data
    if not isinstance(data, dict):
        raise ValueError("Application answer data is malformed.")
    has_snapshot = "step6-custom" in data
    has_program = "_custom_question_program_id" in data
    if has_snapshot != has_program:
        raise ValueError("Custom application question ownership is missing.")
    snapshot = None
    snapshot_program_id = None
    if has_snapshot:
        snapshot_program_id = data["_custom_question_program_id"]
        if (
            type(snapshot_program_id) is not int
            or snapshot_program_id != application.program_id
        ):
            raise ValueError("Custom questions belong to a different program.")
        snapshot = data["step6-custom"]
        validate_custom_question_snapshot(snapshot)
    fixed_answers = data.get("step6-experience", {})
    legacy_answers = data.get("step6", {})
    if not isinstance(fixed_answers, dict) or not isinstance(legacy_answers, dict):
        raise ValueError("Application experience-step data is malformed.")
    state = json.dumps(
        {
            "organization_id": application.organization_id,
            "program_id": application.program_id,
            "snapshot_program_id": snapshot_program_id,
            "snapshot": snapshot,
            "fixed_answers": fixed_answers,
            "legacy_answers": legacy_answers,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return salted_hmac(
        "application-experience-form", state, algorithm="sha256"
    ).hexdigest()


def freeze_custom_question_snapshot(application):
    """Persist one program-bound question set without overwriting newer step data."""
    with transaction.atomic(using=application._state.db):
        current = (
            Application.objects.using(application._state.db)
            .filter(pk=application.pk, organization_id=application.organization_id)
            .select_for_update()
            .first()
        )
        if current is None:
            raise ValueError(
                "The application organization has changed or it is missing."
            )
        if current.program_id != application.program_id:
            raise ValueError("The application program has changed.")
        if current.status not in (
            Application.Status.DRAFT,
            Application.Status.EMAIL_VERIFIED,
            Application.Status.AWAITING_PARENT,
        ):
            raise ValueError("The application is no longer editable.")
        if not current.program_id:
            raise ValueError("An application program is required for custom questions.")
        if current.program.organization_id != current.organization_id:
            raise ValueError(
                "The application program does not belong to its organization."
            )
        data = current.data
        if not isinstance(data, dict):
            raise ValueError("Application answer data is malformed.")
        has_snapshot = "step6-custom" in data
        has_program = "_custom_question_program_id" in data
        if has_snapshot != has_program:
            raise ValueError("Custom application question ownership is missing.")
        if has_snapshot:
            snapshot_program = data["_custom_question_program_id"]
            if (
                type(snapshot_program) is not int
                or snapshot_program != current.program_id
            ):
                raise ValueError("Custom questions belong to a different program.")
            snapshot = data["step6-custom"]
            validate_custom_question_snapshot(snapshot)
            return snapshot
        snapshot = build_custom_question_snapshot(current)
        validate_custom_question_snapshot(snapshot)
        current.data = {
            **data,
            "step6-custom": snapshot,
            "_custom_question_program_id": current.program_id,
        }
        current.save(update_fields=["data", "updated_at"])
        return snapshot


def switch_custom_question_snapshot(application, target_program):
    """Move the active snapshot aside and restore the target program's snapshot."""
    using = application._state.db
    organization_programs = (
        Program.objects.using(using)
        .for_organization(application.organization)
        .filter(organization_id=application.organization_id)
    )
    if not organization_programs.filter(pk=target_program.pk).exists():
        raise ValueError("The selected program does not belong to this organization.")
    if (
        application.program_id
        and not organization_programs.filter(pk=application.program_id).exists()
    ):
        raise ValueError("The current program does not belong to this organization.")

    current_program_id = application.program_id
    target_program_id = target_program.pk
    data = application.data
    if not isinstance(data, dict):
        raise ValueError("Application answer data is malformed.")
    if current_program_id == target_program_id:
        return data

    has_snapshot = "step6-custom" in data
    has_program = "_custom_question_program_id" in data
    if has_snapshot != has_program:
        raise ValueError("Custom application question ownership is missing.")

    active_snapshot = None
    if has_snapshot:
        snapshot_program = data["_custom_question_program_id"]
        if type(snapshot_program) is not int or snapshot_program != current_program_id:
            raise ValueError("Custom questions belong to a different program.")
        active_snapshot = data["step6-custom"]
        validate_custom_question_snapshot(active_snapshot)

    archive = data.get(CUSTOM_QUESTION_SNAPSHOTS_KEY, {})
    if not isinstance(archive, dict):
        raise ValueError("Custom application question archive is malformed.")
    validated_archive = {}
    for program_key, snapshot in archive.items():
        try:
            archived_program_id = int(program_key)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "Custom application question archive is malformed."
            ) from exc
        if (
            not isinstance(program_key, str)
            or str(archived_program_id) != program_key
            or archived_program_id <= 0
            or archived_program_id == current_program_id
        ):
            raise ValueError("Custom application question archive is ambiguous.")
        validate_custom_question_snapshot(snapshot)
        validated_archive[program_key] = snapshot

    updated_data = dict(data)
    if active_snapshot is not None:
        current_program_key = str(current_program_id)
        if current_program_key in validated_archive:
            raise ValueError("The current custom question snapshot is duplicated.")
        validated_archive[current_program_key] = active_snapshot

    target_program_key = str(target_program_id)
    if target_program_key in validated_archive:
        updated_data["step6-custom"] = validated_archive.pop(target_program_key)
        updated_data["_custom_question_program_id"] = target_program_id
    else:
        updated_data.pop("step6-custom", None)
        updated_data.pop("_custom_question_program_id", None)

    if validated_archive:
        updated_data[CUSTOM_QUESTION_SNAPSHOTS_KEY] = validated_archive
    else:
        updated_data.pop(CUSTOM_QUESTION_SNAPSHOTS_KEY, None)
    application.data = updated_data
    return updated_data


def extract_experience_answers(data):
    """Validate and retain distinct answers from both experience-step variants."""
    if not isinstance(data, dict):
        raise ValueError("Application answer data is malformed.")
    answers = []
    seen = set()
    for step in ("step6-experience", "step6"):
        values = data.get(step)
        if values is None:
            continue
        if not isinstance(values, dict):
            raise ValueError("Application experience-step data is malformed.")
        for key, labels in EXPERIENCE_QUESTIONS.items():
            answer = values.get(key)
            if answer is None:
                continue
            if not isinstance(answer, str):
                raise ValueError("An application experience answer is malformed.")
            if not answer.strip():
                continue
            if (key, answer) in seen:
                continue
            seen.add((key, answer))
            answers.append((step, key, labels, answer))

    custom_answers = data.get("step6-custom")
    if custom_answers is not None:
        if not isinstance(custom_answers, list):
            raise ValueError("Custom application answers are malformed.")
        custom_keys = set()
        for item in custom_answers:
            if not isinstance(item, dict):
                raise ValueError("A custom application answer is malformed.")
            question_id = item.get("question_id")
            question_text = item.get("question_text")
            answer = item.get("answer")
            try:
                if not isinstance(question_id, str):
                    raise ValueError
                key = str(UUID(question_id))
            except ValueError as exc:
                raise ValueError(
                    "A custom application question ID is malformed."
                ) from exc
            if key in custom_keys:
                raise ValueError("A custom application question is duplicated.")
            custom_keys.add(key)
            if (
                not isinstance(question_text, str)
                or not question_text.strip()
                or not isinstance(answer, str)
            ):
                raise ValueError("A custom application answer is malformed.")
            if answer.strip():
                answers.append(
                    ("step6-custom", key, (question_text, question_text), answer)
                )
    return answers


def save_experience_answers(application, student, answers):
    """Record extracted answers without replacing existing records."""
    for step, key, labels, answer in answers:
        StudentApplicationAnswer.objects.get_or_create(
            organization=application.organization,
            application=application,
            source_step=step,
            question_key=key,
            defaults={
                "student": student,
                "question_text": labels[application.applicant_type == "parent"],
                "answer": answer,
            },
        )
