"""Preserve answers captured by the existing student application wizard."""

from uuid import UUID

from .models import ProgramApplicationQuestion, StudentApplicationAnswer

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
