from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("programs", "0038_remove_student_race_ethnicity"),
    ]

    operations = [
        migrations.CreateModel(
            name="ProgramFeature",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "key",
                    models.SlugField(
                        help_text="Stable key used in code/templates (e.g., 'discord').",
                        max_length=50,
                        unique=True,
                    ),
                ),
                ("name", models.CharField(max_length=100)),
                ("description", models.TextField(blank=True)),
                ("display_order", models.PositiveSmallIntegerField(default=0)),
            ],
            options={
                "ordering": ["display_order", "name"],
            },
        ),
        migrations.AddField(
            model_name="program",
            name="features",
            field=models.ManyToManyField(
                blank=True,
                help_text="Enable optional features (e.g., Discord, background checks, CMU Andrew ID).",
                related_name="programs",
                to="programs.programfeature",
            ),
        ),
    ]
