from django.db import migrations


def add_tshirt_size_feature(apps, schema_editor):
    ProgramFeature = apps.get_model("programs", "ProgramFeature")
    ProgramFeature.objects.get_or_create(
        key="tshirt-size",
        defaults={
            "name": "T-shirt Sizes",
            "description": "Collect T-shirt size for students during the application process.",
            "display_order": 50,
        },
    )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("programs", "0069_alter_adult_zip_code_alter_school_zip_code_and_more"),
    ]

    operations = [
        migrations.RunPython(add_tshirt_size_feature, noop_reverse),
    ]
