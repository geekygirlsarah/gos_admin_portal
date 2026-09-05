from django.db import migrations


def add_orders_feature(apps, schema_editor):
    ProgramFeature = apps.get_model("programs", "ProgramFeature")
    ProgramFeature.objects.get_or_create(
        key="orders",
        defaults={
            "name": "Order Requests",
            "description": "Enable students to request parts/tools/supplies through the order request system. Mentors can always access it.",
            "display_order": 70,
        },
    )


def remove_orders_feature(apps, schema_editor):
    # Pass on reverse to avoid removing admin-created rows
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("programs", "0112_rename_signout_sheet_feature"),
    ]

    operations = [
        migrations.RunPython(add_orders_feature, remove_orders_feature),
    ]
