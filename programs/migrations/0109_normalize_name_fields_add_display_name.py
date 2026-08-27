from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("programs", "0108_add_badges_feature"),
    ]

    operations = [
        # Student: rename first_name -> preferred_first_name
        migrations.RenameField(
            model_name="student",
            old_name="first_name",
            new_name="preferred_first_name",
        ),
        migrations.AlterField(
            model_name="student",
            name="preferred_first_name",
            field=models.CharField(
                blank=True,
                max_length=150,
                null=True,
                verbose_name="Preferred first name",
            ),
        ),
        # Adult: rename first_name -> legal_first_name
        migrations.RenameField(
            model_name="adult",
            old_name="first_name",
            new_name="legal_first_name",
        ),
        migrations.AlterField(
            model_name="adult",
            name="legal_first_name",
            field=models.CharField(max_length=150, verbose_name="Legal first name"),
        ),
    ]
