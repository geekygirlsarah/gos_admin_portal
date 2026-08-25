from django.db import migrations


def add_badges_feature(apps, schema_editor):
    ProgramFeature = apps.get_model("programs", "ProgramFeature")
    ProgramFeature.objects.get_or_create(
        key="badges",
        defaults={
            "name": "Badges",
            "description": "Enable student badges for this program. Students earn badges that follow them across programs.",
            "display_order": 60,
        },
    )


def remove_badges_feature(apps, schema_editor):
    # Pass on reverse to avoid removing admin-created rows
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("programs", "0107_remove_slidingscale_slidingscale_student_idx_and_more"),
    ]

    operations = [
        migrations.RunPython(add_badges_feature, remove_badges_feature),
    ]
