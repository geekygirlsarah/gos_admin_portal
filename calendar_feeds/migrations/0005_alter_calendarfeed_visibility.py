from django.db import migrations, models


def migrate_invite_only_to_members(apps, schema_editor):
    """Convert any existing invite_only calendar feeds to members."""
    CalendarFeed = apps.get_model("calendar_feeds", "CalendarFeed")
    CalendarFeed.objects.filter(visibility="invite_only").update(visibility="members")


class Migration(migrations.Migration):

    dependencies = [
        ("calendar_feeds", "0004_alter_calendarfeed_source_type"),
    ]

    operations = [
        migrations.RunPython(
            migrate_invite_only_to_members,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="calendarfeed",
            name="visibility",
            field=models.CharField(
                choices=[
                    ("anonymous", "Public (no login required)"),
                    ("members", "Members (logged-in users)"),
                ],
                default="members",
                max_length=20,
            ),
        ),
    ]
