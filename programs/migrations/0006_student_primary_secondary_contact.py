import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("programs", "0005_parent"),
    ]

    operations = [
        migrations.AddField(
            model_name="student",
            name="primary_contact",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="primary_for_students",
                to="programs.parent",
            ),
        ),
        migrations.AddField(
            model_name="student",
            name="secondary_contact",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="secondary_for_students",
                to="programs.parent",
            ),
        ),
    ]
