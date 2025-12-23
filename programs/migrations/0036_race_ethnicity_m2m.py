from django.db import migrations, models


def seed_race_ethnicity_options(apps, schema_editor):
    RaceEthnicity = apps.get_model("programs", "RaceEthnicity")
    options = [
        ("american-indian-or-alaska-native", "American Indian or Alaska Native", 1),
        ("asian", "Asian", 2),
        ("black-or-african-american", "Black or African-American", 3),
        ("hispanic-or-latino", "Hispanic or Latino", 4),
        ("middle-eastern-or-north-african", "Middle Eastern or North African", 5),
        (
            "native-hawaiian-or-other-pacific-islander",
            "Native Hawaiian or Other Pacific Islander",
            6,
        ),
        ("white", "White", 7),
        ("other", "Other", 99),
    ]
    for key, name, order in options:
        RaceEthnicity.objects.get_or_create(
            key=key, defaults={"name": name, "display_order": order}
        )


class Migration(migrations.Migration):

    dependencies = [
        ("programs", "0035_student_allergies_student_dietary_restrictions_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="RaceEthnicity",
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
                ("key", models.SlugField(max_length=50, unique=True)),
                ("name", models.CharField(max_length=100, unique=True)),
                ("display_order", models.PositiveSmallIntegerField(default=0)),
            ],
            options={
                "verbose_name_plural": "Race/Ethnicity Options",
                "ordering": ["display_order", "name"],
            },
        ),
        migrations.AddField(
            model_name="student",
            name="race_ethnicities",
            field=models.ManyToManyField(
                blank=True,
                related_name="students",
                to="programs.raceethnicity",
                verbose_name="Race / Ethnicity",
            ),
        ),
        migrations.RunPython(seed_race_ethnicity_options, migrations.RunPython.noop),
    ]
