import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("programs", "0025_program_start_end_dates"),
    ]

    operations = [
        migrations.CreateModel(
            name="StudentApplication",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("legal_first_name", models.CharField(max_length=150)),
                ("first_name", models.CharField(blank=True, max_length=150, null=True)),
                ("last_name", models.CharField(max_length=150)),
                ("pronouns", models.CharField(blank=True, max_length=50, null=True)),
                ("date_of_birth", models.DateField(blank=True, null=True)),
                ("address", models.CharField(blank=True, max_length=255, null=True)),
                ("city", models.CharField(blank=True, max_length=100, null=True)),
                ("state", models.CharField(blank=True, max_length=50, null=True)),
                ("zip_code", models.CharField(blank=True, max_length=20, null=True)),
                (
                    "cell_phone_number",
                    models.CharField(blank=True, max_length=30, null=True),
                ),
                (
                    "personal_email",
                    models.EmailField(blank=True, max_length=254, null=True),
                ),
                ("andrew_id", models.CharField(blank=True, max_length=50, null=True)),
                (
                    "andrew_email",
                    models.EmailField(blank=True, max_length=254, null=True),
                ),
                (
                    "graduation_year",
                    models.PositiveSmallIntegerField(blank=True, null=True),
                ),
                (
                    "race_ethnicity",
                    models.CharField(blank=True, max_length=100, null=True),
                ),
                ("tshirt_size", models.CharField(blank=True, max_length=10, null=True)),
                ("on_discord", models.BooleanField(default=False)),
                (
                    "discord_handle",
                    models.CharField(blank=True, max_length=100, null=True),
                ),
                (
                    "parent_name",
                    models.CharField(blank=True, max_length=200, null=True),
                ),
                (
                    "parent_email",
                    models.EmailField(blank=True, max_length=254, null=True),
                ),
                (
                    "parent_phone",
                    models.CharField(blank=True, max_length=30, null=True),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("accepted", "Accepted"),
                            ("rejected", "Rejected"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=10,
                    ),
                ),
                ("notes", models.TextField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "program",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="applications",
                        to="programs.program",
                    ),
                ),
                (
                    "school",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="applications",
                        to="programs.school",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
    ]
