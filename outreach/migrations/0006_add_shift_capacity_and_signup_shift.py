import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("outreach", "0005_remove_outreachevent_dates"),
    ]

    operations = [
        migrations.AddField(
            model_name="outreachshift",
            name="max_champions",
            field=models.PositiveIntegerField(
                default=1, verbose_name="Number of champions"
            ),
        ),
        migrations.AddField(
            model_name="outreachshift",
            name="max_helpers",
            field=models.PositiveIntegerField(
                default=5, verbose_name="Number of signups"
            ),
        ),
        migrations.AddField(
            model_name="outreachsignup",
            name="shift",
            field=models.ForeignKey(
                null=True,
                blank=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="signups",
                to="outreach.outreachshift",
            ),
        ),
    ]
