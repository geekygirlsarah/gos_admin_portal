from django.db import migrations


def seed_program_features(apps, schema_editor):
    ProgramFeature = apps.get_model("programs", "ProgramFeature")
    defaults = [
        {
            "key": "discord",
            "name": "Discord",
            "description": "Show and collect Discord fields for students and mentors.",
            "display_order": 10,
        },
        {
            "key": "background-checks",
            "name": "Background Checks",
            "description": "Enable background clearance fields and related UI/logic.",
            "display_order": 20,
        },
        {
            "key": "cmu-andrew",
            "name": "CMU Andrew ID",
            "description": "Show CMU Andrew ID and email fields.",
            "display_order": 30,
        },
    ]
    for d in defaults:
        ProgramFeature.objects.get_or_create(key=d["key"], defaults=d)


def unseed_program_features(apps, schema_editor):
    # Do not delete on reverse to avoid removing admin-created rows accidentally
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("programs", "0039_programfeature_m2m"),
    ]

    operations = [
        migrations.RunPython(seed_program_features, unseed_program_features),
    ]
