from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('programs', '0024_alumni'),
    ]

    operations = [
        migrations.AddField(
            model_name='program',
            name='start_date',
            field=models.DateField(blank=True, null=True, db_index=True, help_text='Program start date'),
        ),
        migrations.AddField(
            model_name='program',
            name='end_date',
            field=models.DateField(blank=True, null=True, db_index=True, help_text='Program end date'),
        ),
    ]
