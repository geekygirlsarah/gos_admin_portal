import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("outreach", "0002_alter_outreachevent_options_outreachevent_program"),
    ]

    operations = [
        migrations.CreateModel(
            name="OutreachShift",
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
                ("date", models.DateField()),
                ("start_time", models.TimeField()),
                ("end_time", models.TimeField()),
                (
                    "event",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="shifts",
                        to="outreach.outreachevent",
                    ),
                ),
            ],
            options={
                "ordering": ["date", "start_time"],
            },
        ),
    ]
