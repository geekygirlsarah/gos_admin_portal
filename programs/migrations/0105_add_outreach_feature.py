from django.db import migrations


def add_outreach_feature(apps, schema_editor):
    ProgramFeature = apps.get_model("programs", "ProgramFeature")
    ProgramFeature.objects.get_or_create(
        key="outreach",
        defaults={
            "name": "Outreach",
            "description": "Enable community outreach events and student signups for this program.",
            "display_order": 50,
        },
    )


def remove_outreach_feature(apps, schema_editor):
    # Pass on reverse to avoid removing admin-created rows
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("programs", "0104_school_district"),
    ]

    operations = [
        migrations.RunPython(add_outreach_feature, remove_outreach_feature),
    ]
