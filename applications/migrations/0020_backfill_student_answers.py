import logging

from django.db import migrations

QUESTIONS = {
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


def backfill_answers(apps, schema_editor):
    Application = apps.get_model("applications", "Application")
    Answer = apps.get_model("applications", "StudentApplicationAnswer")
    alias = schema_editor.connection.alias
    applications = (
        Application.objects.using(alias)
        .filter(status__in=("approved", "approved_signed", "converted"))
        .exclude(applicant_type="mentor")
        .select_related("converted_student", "program")
    )
    unlinked = inconsistent = malformed = 0
    for application in applications.iterator(chunk_size=500):
        if not application.converted_student_id:
            unlinked += 1
            continue
        if (
            not application.organization_id
            or application.converted_student.organization_id
            != application.organization_id
            or (
                application.program_id
                and application.program.organization_id != application.organization_id
            )
        ):
            inconsistent += 1
            continue
        data = application.data
        if not isinstance(data, dict):
            malformed += 1
            continue
        seen = set()
        has_malformed = False
        for step in ("step6-experience", "step6"):
            values = data.get(step)
            if values is None:
                continue
            if not isinstance(values, dict):
                has_malformed = True
                continue
            for key, labels in QUESTIONS.items():
                answer = values.get(key)
                if answer is None:
                    continue
                if not isinstance(answer, str):
                    has_malformed = True
                    continue
                if not answer.strip():
                    continue
                if (key, answer) in seen:
                    continue
                seen.add((key, answer))
                Answer.objects.using(alias).get_or_create(
                    application_id=application.pk,
                    source_step=step,
                    question_key=key,
                    defaults={
                        "organization_id": application.organization_id,
                        "student_id": application.converted_student_id,
                        "question_text": labels[application.applicant_type == "parent"],
                        "answer": answer,
                    },
                )
        malformed += has_malformed
    if unlinked or inconsistent or malformed:
        logging.getLogger(__name__).warning(
            "Student application answers: %s approved/converted applications have "
            "no linked student, %s have inconsistent ownership, and %s contain "
            "malformed answer data; their original data was left unchanged for review.",
            unlinked,
            inconsistent,
            malformed,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("applications", "0019_studentapplicationanswer"),
    ]

    operations = [
        migrations.RunPython(backfill_answers, migrations.RunPython.noop),
    ]
