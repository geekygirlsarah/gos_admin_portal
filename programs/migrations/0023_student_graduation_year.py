import datetime

from django.db import migrations, models


def grade_to_grad_year(apps, schema_editor):
    Student = apps.get_model("programs", "Student")
    today = datetime.date.today()
    # Determine current school year end (June-end). If July-Dec, end year is current year + 1; else current year
    end_year = today.year + (1 if today.month >= 7 else 0)
    for s in Student.objects.all():
        # old field 'grade' may or may not exist depending on migration state; use getattr defensively
        grade = getattr(s, "grade", None)
        if grade is None:
            continue
        try:
            g = int(grade)
        except (TypeError, ValueError):
            continue
        if g < 0:
            continue
        # K is 0 -> 13 years remaining including K
        if g == 0:
            gy = end_year + 13
        else:
            # grades 1-12
            gy = end_year + max(0, 12 - g)
        s.graduation_year = gy
        s.save(update_fields=["graduation_year"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("programs", "0022_alter_student_first_name_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="student",
            name="graduation_year",
            field=models.PositiveSmallIntegerField(
                blank=True, null=True, help_text="Expected high school graduation year"
            ),
        ),
        migrations.RunPython(grade_to_grad_year, reverse_code=noop),
        migrations.RemoveField(
            model_name="student",
            name="grade",
        ),
    ]
