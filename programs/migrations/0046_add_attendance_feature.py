from django.db import migrations


def add_attendance_feature(apps, schema_editor):
    ProgramFeature = apps.get_model("programs", "ProgramFeature")
    # Create attendance feature if it doesn't exist
    ProgramFeature.objects.get_or_create(
        key="attendance",
        defaults={
            "name": "Attendance",
            "description": "Enable attendance tracking (RFID/visitor taps, sessions, and reports) for this program.",
            "display_order": 10,
        },
    )


def remove_attendance_feature(apps, schema_editor):
    ProgramFeature = apps.get_model("programs", "ProgramFeature")
    try:
        obj = ProgramFeature.objects.get(key="attendance")
        # Do not delete if referenced by any programs; just keep it to avoid dangling M2M
        # If you really want to remove, uncomment the delete below.
        # obj.delete()
    except ProgramFeature.DoesNotExist:
        pass


class Migration(migrations.Migration):

    dependencies = [
        ("programs", "0045_slidingscale_date"),
    ]

    operations = [
        migrations.RunPython(add_attendance_feature, remove_attendance_feature),
    ]
