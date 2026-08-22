import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("outreach", "0007_migrate_signups_and_capacity_to_shifts"),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name="outreachsignup",
            unique_together=set(),
        ),
        migrations.AlterField(
            model_name="outreachsignup",
            name="shift",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="signups",
                to="outreach.outreachshift",
            ),
        ),
        migrations.RemoveField(
            model_name="outreachsignup",
            name="event",
        ),
        migrations.AlterUniqueTogether(
            name="outreachsignup",
            unique_together={("student", "shift")},
        ),
        migrations.RemoveField(
            model_name="outreachevent",
            name="max_champions",
        ),
        migrations.RemoveField(
            model_name="outreachevent",
            name="max_helpers",
        ),
    ]
