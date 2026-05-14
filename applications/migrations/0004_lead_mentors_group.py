"""Create the 'Lead Mentors' group and grant it the
``applications.review_application`` permission so members can use the
custom Applications review pages.
"""
from __future__ import annotations

from django.db import migrations


LEAD_MENTORS_GROUP = "Lead Mentors"
REVIEW_PERM_CODENAME = "review_application"


def create_group(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")

    app_ct = ContentType.objects.filter(
        app_label="applications", model="application"
    ).first()
    if app_ct is None:
        # Application model hasn't been migrated yet — shouldn't happen
        # since 0003 runs first, but be defensive.
        return

    perm = Permission.objects.filter(
        content_type=app_ct, codename=REVIEW_PERM_CODENAME
    ).first()

    group, _ = Group.objects.get_or_create(name=LEAD_MENTORS_GROUP)
    if perm is not None:
        group.permissions.add(perm)


def remove_group(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name=LEAD_MENTORS_GROUP).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("applications", "0003_alter_application_options"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        migrations.RunPython(create_group, remove_group),
    ]
