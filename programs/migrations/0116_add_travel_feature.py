from django.db import migrations


def add_travel_feature(apps, schema_editor):
    ProgramFeature = apps.get_model("programs", "ProgramFeature")
    ProgramFeature.objects.get_or_create(
        key="travel",
        defaults={
            "name": "Travel",
            "description": (
                "Enable the Travel section: trip info, student signups with "
                "parent approval, mentor signups, and cost check-lists."
            ),
            "display_order": 90,
        },
    )


def remove_travel_feature(apps, schema_editor):
    # Pass on reverse to avoid removing admin-created rows
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("programs", "0115_alter_rolepermission_section"),
    ]

    operations = [
        migrations.RunPython(add_travel_feature, remove_travel_feature),
    ]
