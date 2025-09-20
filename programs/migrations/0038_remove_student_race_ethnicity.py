from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('programs', '0037_migrate_student_race_to_m2m'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='student',
            name='race_ethnicity',
        ),
    ]
