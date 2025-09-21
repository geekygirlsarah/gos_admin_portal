from django.db import migrations


def add_signout_feature(apps, schema_editor):
    ProgramFeature = apps.get_model('programs', 'ProgramFeature')
    ProgramFeature.objects.get_or_create(
        key='signout-sheet',
        defaults={
            'name': 'Printable Sign-out Sheet',
            'description': 'Enable a printable sign-out sheet for parents/guardians on the Program page.',
            'display_order': 40,
        }
    )


def noop_reverse(apps, schema_editor):
    # Do not delete on reverse to avoid removing admin-created rows
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('programs', '0040_seed_program_features'),
    ]

    operations = [
        migrations.RunPython(add_signout_feature, noop_reverse),
    ]
