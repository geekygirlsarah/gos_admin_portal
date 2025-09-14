from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('programs', '0026_studentapplication'),
    ]

    operations = [
        migrations.AddField(
            model_name='school',
            name='district',
            field=models.CharField(max_length=150, blank=True, null=True, verbose_name='School district'),
        ),
        migrations.AddField(
            model_name='school',
            name='address',
            field=models.CharField(max_length=255, blank=True, null=True),
        ),
    ]
