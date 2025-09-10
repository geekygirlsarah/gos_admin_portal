from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('programs', '0020_feeassignment_remove_override_waived'),
    ]

    operations = [
        migrations.RenameField(
            model_name='student',
            old_name='first_name',
            new_name='legal_first_name',
        ),
        migrations.RenameField(
            model_name='student',
            old_name='preferred_first_name',
            new_name='first_name',
        ),
    ]
