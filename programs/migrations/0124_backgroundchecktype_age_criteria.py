import datetime

from django.db import migrations, models


def migrate_min_age_to_by_date(apps, schema_editor):
    BackgroundCheckType = apps.get_model("programs", "BackgroundCheckType")
    for bct in BackgroundCheckType.objects.all():
        if (
            bct.student_min_age_by_date is not None
            and bct.student_min_age_cutoff_date is None
        ):
            bct.student_min_age_cutoff_date = datetime.date(2000, 9, 1)
            bct.save(update_fields=["student_min_age_cutoff_date"])


def reverse_migrate_min_age(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("programs", "0123_background_check_type"),
    ]

    operations = [
        migrations.RenameField(
            model_name="backgroundchecktype",
            old_name="student_min_age",
            new_name="student_min_age_by_date",
        ),
        migrations.AlterField(
            model_name="backgroundchecktype",
            name="student_min_age_by_date",
            field=models.PositiveIntegerField(
                blank=True,
                help_text="For students: minimum age reached by the specified cutoff date (e.g. 17).",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="backgroundchecktype",
            name="student_min_age_cutoff_date",
            field=models.DateField(
                blank=True,
                help_text="Cutoff date (month and day used annually) for the minimum age requirement (e.g. Sept 1).",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="backgroundchecktype",
            name="student_min_age_during_program",
            field=models.PositiveIntegerField(
                blank=True,
                help_text="For students: minimum age reached during an active program enrollment (e.g. 18).",
                null=True,
            ),
        ),
        migrations.RunPython(
            migrate_min_age_to_by_date,
            reverse_code=reverse_migrate_min_age,
        ),
    ]
