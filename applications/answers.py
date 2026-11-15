"""Preserve answers captured by the existing student application wizard."""

from .models import StudentApplicationAnswer

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
