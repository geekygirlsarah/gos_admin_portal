from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("programs", "0015_slidingscale_amount_to_percent"),
    ]

    operations = [
        migrations.AddField(
            model_name="fee",
            name="date",
            field=models.DateField(
                blank=True,
                null=True,
                help_text="Date the fee was posted/received (used for balance sheet sorting).",
            ),
        ),
    ]
